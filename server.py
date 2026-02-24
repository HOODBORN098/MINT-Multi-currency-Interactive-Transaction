"""
ChainPay REST API Server — v3.0 (M-Pesa Edition)
==================================================
Complete server with M-Pesa STK Push (Daraja API), reversal system,
help panel, and all original functionality preserved.

Usage:
    python server.py                        # HTTPS (auto-generates self-signed cert)
    CHAINPAY_HTTP=1 python server.py        # Plain HTTP (development)

M-Pesa credentials are embedded directly below.
For production: move to environment variables.

API Docs: http://localhost:8443/docs
"""

import sys
import os
import time
import json
import uuid
import base64
import hashlib
import logging
import asyncio
import threading
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional, List, Dict, Any

# ── Path setup ────────────────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import (
    FastAPI, Depends, HTTPException, Request,
    Query, Path as FPath, BackgroundTasks
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
import uvicorn

# ── Core imports ──────────────────────────────────────────────────────────────
try:
    from core import database as db
    from core.database import init_db
    from core.wallet import WalletService, FXEngine, calculate_fee, format_amount
    from core.security import (
        get_session_manager, verify_password, hash_password, generate_keypair
    )
    from core.blockchain import get_blockchain, Transaction as BCTransaction
except ModuleNotFoundError:
    # Flat layout fallback (all files in same directory)
    import database as db
    from database import init_db
    from wallet import WalletService, FXEngine, calculate_fee, format_amount
    from security import (
        get_session_manager, verify_password, hash_password, generate_keypair
    )
    from blockchain import get_blockchain, Transaction as BCTransaction

import urllib.request
import urllib.error

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s"
)
logger = logging.getLogger("chainpay")

# =============================================================================
# M-PESA CONFIGURATION
# =============================================================================

MPESA_CONFIG = {
    "consumer_key":     os.environ.get("MPESA_CONSUMER_KEY",
                        "xyP45fkLaXWfl0FtQG9bN5WL1tnjFqMEsmDhiQ3vhZce4WoE"),
    "consumer_secret":  os.environ.get("MPESA_CONSUMER_SECRET",
                        "vCvIeK8mefSyPEvAFQxyiqt0rVOLE1af8ItNZAWNKhREkmeVfgiegANkoI5hLCGY"),
    "shortcode":        os.environ.get("MPESA_SHORTCODE", "174379"),
    "passkey":          os.environ.get("MPESA_PASSKEY",
                        "bfb279f9aa9bdbcf158e97dd71a467cd2e0c893059b10f78e6b72ada1ed2c919"),
    "callback_url":     os.environ.get("MPESA_CALLBACK_URL",
                        "https://uncommanded-micaela-multiplicatively.ngrok-free.dev/api/v1/mpesa/callback"),
    "environment":      os.environ.get("MPESA_ENV", "sandbox"),
    "account_ref":      "ChainPay",
    "transaction_desc": "Wallet Deposit",
}

MPESA_SANDBOX_URL    = "https://sandbox.safaricom.co.ke"
MPESA_PRODUCTION_URL = "https://api.safaricom.co.ke"


def _mpesa_base_url() -> str:
    return (MPESA_PRODUCTION_URL
            if MPESA_CONFIG["environment"] == "production"
            else MPESA_SANDBOX_URL)


# ── OAuth token cache ─────────────────────────────────────────────────────────

class _TokenCache:
    token: Optional[str] = None
    expires_at: float = 0.0

    @classmethod
    def get(cls) -> Optional[str]:
        if cls.token and time.time() < cls.expires_at - 60:
            return cls.token
        return None

    @classmethod
    def set(cls, token: str, expires_in: int = 3600):
        cls.token = token
        cls.expires_at = time.time() + expires_in


def _get_mpesa_token() -> str:
    """
    Fetch (or return cached) Daraja OAuth 2.0 bearer token.
    Token is valid for 3600 seconds; refreshed automatically.
    """
    cached = _TokenCache.get()
    if cached:
        return cached

    key    = MPESA_CONFIG["consumer_key"]
    secret = MPESA_CONFIG["consumer_secret"]
    creds  = base64.b64encode(f"{key}:{secret}".encode()).decode()
    url    = _mpesa_base_url() + "/oauth/v1/generate?grant_type=client_credentials"

    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Basic {creds}"},
        method="GET"
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data       = json.loads(resp.read())
            token      = data["access_token"]
            expires_in = int(data.get("expires_in", 3600))
            _TokenCache.set(token, expires_in)
            logger.info("M-Pesa OAuth token refreshed successfully.")
            return token
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        raise RuntimeError(f"M-Pesa token fetch failed ({e.code}): {body}")
    except Exception as e:
        raise RuntimeError(f"M-Pesa token request error: {e}")


def _mpesa_password(shortcode: str, passkey: str, timestamp: str) -> str:
    """Daraja STK Push password = Base64(Shortcode + Passkey + Timestamp)."""
    return base64.b64encode((shortcode + passkey + timestamp).encode()).decode()


def _normalize_phone(phone: str) -> str:
    """Normalize any Kenyan phone format to 254XXXXXXXXX."""
    phone = phone.strip().replace(" ", "").replace("-", "")
    if phone.startswith("+"):
        phone = phone[1:]
    if phone.startswith("0"):
        phone = "254" + phone[1:]
    if not phone.startswith("254"):
        phone = "254" + phone
    return phone


def _validate_kenyan_phone(phone: str) -> str:
    """Raise ValueError if phone is not a valid Kenyan M-Pesa number. Returns normalized."""
    normalized = _normalize_phone(phone)
    if len(normalized) != 12 or not normalized.isdigit():
        raise ValueError(f"Invalid phone number format: {phone}")
    prefix = normalized[3:5]
    valid  = {"70","71","72","74","75","76","77","78","79","11","10"}
    if prefix not in valid:
        raise ValueError(f"Not a recognized Safaricom number (prefix 0{prefix})")
    return normalized


def _initiate_stk_push(phone: str, amount: int, internal_ref: str) -> dict:
    """
    Call Daraja STK Push API.
    Returns Safaricom response dict with CheckoutRequestID on success.
    Raises RuntimeError on failure.
    """
    token     = _get_mpesa_token()
    shortcode = MPESA_CONFIG["shortcode"]
    passkey   = MPESA_CONFIG["passkey"]
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    password  = _mpesa_password(shortcode, passkey, timestamp)
    phone     = _normalize_phone(phone)

    payload = {
        "BusinessShortCode": shortcode,
        "Password":          password,
        "Timestamp":         timestamp,
        "TransactionType":   "CustomerPayBillOnline",
        "Amount":            int(amount),
        "PartyA":            phone,
        "PartyB":            shortcode,
        "PhoneNumber":       phone,
        "CallBackURL":       MPESA_CONFIG["callback_url"],
        "AccountReference":  MPESA_CONFIG["account_ref"][:12],
        "TransactionDesc":   MPESA_CONFIG["transaction_desc"][:13],
    }

    url  = _mpesa_base_url() + "/mpesa/stkpush/v1/processrequest"
    data = json.dumps(payload).encode()
    req  = urllib.request.Request(
        url, data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type":  "application/json",
        },
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
            logger.info(
                f"STK Push initiated: {result.get('CheckoutRequestID')} "
                f"| ref={internal_ref} | phone={phone} | amount={amount}"
            )
            return result
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        logger.error(f"STK Push HTTP error ({e.code}): {body}")
        raise RuntimeError(f"STK Push failed ({e.code}): {body}")
    except Exception as e:
        logger.error(f"STK Push request error: {e}")
        raise RuntimeError(f"STK Push request error: {e}")


def _parse_mpesa_callback(body: dict) -> dict:
    """
    Parse Safaricom STK Push callback body into a clean normalized dict.
    Always returns a dict — never raises.
    """
    try:
        stk             = body["Body"]["stkCallback"]
        result_code     = int(stk.get("ResultCode", -1))
        result_desc     = stk.get("ResultDesc", "Unknown")
        checkout_id     = stk.get("CheckoutRequestID", "")
        merchant_id     = stk.get("MerchantRequestID", "")

        result = {
            "success":             result_code == 0,
            "result_code":         result_code,
            "result_desc":         result_desc,
            "checkout_request_id": checkout_id,
            "merchant_request_id": merchant_id,
            "amount":              None,
            "receipt_number":      None,
            "phone":               None,
            "tx_date":             None,
        }

        if result_code == 0:
            items = stk.get("CallbackMetadata", {}).get("Item", [])
            meta  = {item["Name"]: item.get("Value") for item in items}
            result["amount"]         = float(meta.get("Amount", 0))
            result["receipt_number"] = str(meta.get("MpesaReceiptNumber", ""))
            result["phone"]          = str(meta.get("PhoneNumber", ""))
            result["tx_date"]        = str(meta.get("TransactionDate", ""))

        return result
    except Exception as e:
        logger.error(f"Callback parse error: {e}")
        return {
            "success": False, "result_code": -99,
            "result_desc": f"Parse error: {e}",
            "checkout_request_id": "", "merchant_request_id": "",
            "amount": None, "receipt_number": None,
            "phone": None, "tx_date": None,
        }


MPESA_ERROR_MESSAGES = {
    0:    "Payment received successfully.",
    1:    "Insufficient funds in your M-Pesa account.",
    17:   "M-Pesa system temporarily unavailable. Please try again.",
    1032: "Request cancelled by user.",
    1037: "STK Push timed out — you did not enter your PIN in time.",
    2001: "Wrong PIN entered.",
    -1:   "System internal error.",
}


# =============================================================================
# M-PESA DATABASE SCHEMA & CRUD
# =============================================================================

MPESA_EXTRA_SCHEMA = """
CREATE TABLE IF NOT EXISTS mpesa_transactions (
    mpesa_tx_id         TEXT PRIMARY KEY,
    internal_ref        TEXT UNIQUE NOT NULL,
    user_id             TEXT NOT NULL,
    phone               TEXT NOT NULL,
    amount_kes          INTEGER NOT NULL,
    checkout_request_id TEXT UNIQUE,
    merchant_request_id TEXT,
    status              TEXT DEFAULT 'PENDING',
    result_code         INTEGER,
    result_desc         TEXT,
    mpesa_receipt       TEXT UNIQUE,
    mpesa_phone         TEXT,
    wallet_credited     INTEGER DEFAULT 0,
    chainpay_tx_id      TEXT,
    initiated_at        REAL NOT NULL,
    callback_at         REAL,
    expires_at          REAL NOT NULL,
    raw_callback        TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS reversal_requests (
    reversal_id  TEXT PRIMARY KEY,
    tx_id        TEXT NOT NULL,
    requester_id TEXT NOT NULL,
    reason       TEXT DEFAULT '',
    status       TEXT DEFAULT 'PENDING',
    admin_id     TEXT,
    admin_note   TEXT DEFAULT '',
    created_at   REAL NOT NULL,
    reviewed_at  REAL,
    completed_at REAL
);

CREATE INDEX IF NOT EXISTS idx_mpesa_user      ON mpesa_transactions(user_id);
CREATE INDEX IF NOT EXISTS idx_mpesa_status    ON mpesa_transactions(status);
CREATE INDEX IF NOT EXISTS idx_mpesa_checkout  ON mpesa_transactions(checkout_request_id);
CREATE INDEX IF NOT EXISTS idx_reversal_tx     ON reversal_requests(tx_id);
CREATE INDEX IF NOT EXISTS idx_reversal_status ON reversal_requests(status);
CREATE INDEX IF NOT EXISTS idx_reversal_user   ON reversal_requests(requester_id);
"""

import sqlite3


def _mpesa_conn():
    """Direct SQLite connection for M-Pesa operations (used outside get_db context)."""
    conn = sqlite3.connect(db.DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _init_mpesa_schema():
    with db.get_db() as conn:
        conn.executescript(MPESA_EXTRA_SCHEMA)
    logger.info("M-Pesa schema ready.")


# ── M-Pesa DB helpers ─────────────────────────────────────────────────────────

def _mpesa_create_pending(user_id: str, phone: str, amount_kes: float) -> dict:
    """Create a PENDING record before calling Safaricom. Returns the record."""
    mpesa_tx_id  = str(uuid.uuid4())
    internal_ref = str(uuid.uuid4())
    now          = time.time()
    expires_at   = now + 120

    with db.get_db() as conn:
        conn.execute(
            """INSERT INTO mpesa_transactions
               (mpesa_tx_id, internal_ref, user_id, phone, amount_kes,
                status, initiated_at, expires_at)
               VALUES (?,?,?,?,?,'PENDING',?,?)""",
            (mpesa_tx_id, internal_ref, user_id, phone,
             int(round(amount_kes * 100)), now, expires_at)
        )
    return {
        "mpesa_tx_id":  mpesa_tx_id,
        "internal_ref": internal_ref,
        "amount_kes":   amount_kes,
    }


def _mpesa_set_checkout_id(internal_ref: str, checkout_id: str, merchant_id: str):
    conn = _mpesa_conn()
    try:
        conn.execute(
            "UPDATE mpesa_transactions SET checkout_request_id=?, merchant_request_id=? WHERE internal_ref=?",
            (checkout_id, merchant_id, internal_ref)
        )
        conn.commit()
    finally:
        conn.close()


def _mpesa_get_by_checkout(checkout_id: str) -> Optional[dict]:
    conn = _mpesa_conn()
    try:
        row = conn.execute(
            "SELECT * FROM mpesa_transactions WHERE checkout_request_id=?", (checkout_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _mpesa_get_by_receipt(receipt: str) -> Optional[dict]:
    conn = _mpesa_conn()
    try:
        row = conn.execute(
            "SELECT * FROM mpesa_transactions WHERE mpesa_receipt=?", (receipt,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _mpesa_get_by_ref(internal_ref: str, user_id: str) -> Optional[dict]:
    conn = _mpesa_conn()
    try:
        row = conn.execute(
            "SELECT * FROM mpesa_transactions WHERE internal_ref=? AND user_id=?",
            (internal_ref, user_id)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _mpesa_confirm(internal_ref: str, result_code: int, result_desc: str,
                   receipt: str, mpesa_phone: str, chainpay_tx_id: str,
                   raw_callback: dict):
    conn = _mpesa_conn()
    try:
        conn.execute(
            """UPDATE mpesa_transactions SET
               status='CONFIRMED', result_code=?, result_desc=?,
               mpesa_receipt=?, mpesa_phone=?, wallet_credited=1,
               chainpay_tx_id=?, callback_at=?, raw_callback=?
               WHERE internal_ref=?""",
            (result_code, result_desc, receipt, mpesa_phone,
             chainpay_tx_id, time.time(), json.dumps(raw_callback), internal_ref)
        )
        conn.commit()
    finally:
        conn.close()


def _mpesa_fail(internal_ref: str, result_code: int, result_desc: str, raw: dict):
    conn = _mpesa_conn()
    try:
        conn.execute(
            """UPDATE mpesa_transactions SET
               status='FAILED', result_code=?, result_desc=?, callback_at=?, raw_callback=?
               WHERE internal_ref=?""",
            (result_code, result_desc, time.time(), json.dumps(raw), internal_ref)
        )
        conn.commit()
    finally:
        conn.close()


def _mpesa_expire_stale():
    conn = _mpesa_conn()
    try:
        cur = conn.execute(
            "UPDATE mpesa_transactions SET status='EXPIRED' WHERE status='PENDING' AND expires_at < ?",
            (time.time(),)
        )
        conn.commit()
        if cur.rowcount:
            logger.info(f"Expired {cur.rowcount} stale M-Pesa pending records.")
    finally:
        conn.close()


def _mpesa_user_history(user_id: str, limit: int = 50) -> List[dict]:
    conn = _mpesa_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM mpesa_transactions WHERE user_id=? ORDER BY initiated_at DESC LIMIT ?",
            (user_id, limit)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _mpesa_all(status: Optional[str] = None, limit: int = 200) -> List[dict]:
    conn = _mpesa_conn()
    try:
        if status:
            rows = conn.execute(
                "SELECT * FROM mpesa_transactions WHERE status=? ORDER BY initiated_at DESC LIMIT ?",
                (status, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM mpesa_transactions ORDER BY initiated_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── Reversal DB helpers ───────────────────────────────────────────────────────

def _reversal_create(tx_id: str, requester_id: str, reason: str) -> dict:
    reversal_id = str(uuid.uuid4())
    now         = time.time()
    with db.get_db() as conn:
        existing = conn.execute(
            "SELECT reversal_id FROM reversal_requests WHERE tx_id=? AND status IN ('PENDING','APPROVED')",
            (tx_id,)
        ).fetchone()
        if existing:
            raise ValueError("A reversal request for this transaction already exists.")
        conn.execute(
            """INSERT INTO reversal_requests
               (reversal_id, tx_id, requester_id, reason, status, created_at)
               VALUES (?,?,?,?,'PENDING',?)""",
            (reversal_id, tx_id, requester_id, reason, now)
        )
    return {"reversal_id": reversal_id, "tx_id": tx_id, "status": "PENDING"}


def _reversal_get(reversal_id: str) -> Optional[dict]:
    conn = _mpesa_conn()
    try:
        row = conn.execute(
            "SELECT * FROM reversal_requests WHERE reversal_id=?", (reversal_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _reversal_pending_list() -> List[dict]:
    conn = _mpesa_conn()
    try:
        rows = conn.execute(
            """SELECT rr.*, t.amount, t.currency, t.sender, t.recipient,
                      u.name as requester_name, u.phone as requester_phone
               FROM reversal_requests rr
               JOIN transactions t ON rr.tx_id = t.tx_id
               JOIN users u ON rr.requester_id = u.user_id
               WHERE rr.status='PENDING' ORDER BY rr.created_at ASC"""
        ).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            d["amount"] = d["amount"] / 100.0
            results.append(d)
        return results
    finally:
        conn.close()


def _reversal_history(limit: int = 200) -> List[dict]:
    conn = _mpesa_conn()
    try:
        rows = conn.execute(
            """SELECT rr.*, t.amount, t.currency, t.sender, t.recipient,
                      u.name as requester_name, u.phone as requester_phone
               FROM reversal_requests rr
               JOIN transactions t ON rr.tx_id = t.tx_id
               JOIN users u ON rr.requester_id = u.user_id
               ORDER BY rr.created_at DESC LIMIT ?""", (limit,)
        ).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            d["amount"] = d["amount"] / 100.0
            results.append(d)
        return results
    finally:
        conn.close()


def _get_tx_by_id(tx_id: str) -> Optional[dict]:
    """Fetch a single transaction record from the main transactions table."""
    conn = _mpesa_conn()
    try:
        row = conn.execute(
            "SELECT * FROM transactions WHERE tx_id=?", (tx_id,)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            d["metadata"] = json.loads(d.get("metadata") or "{}")
        except Exception:
            d["metadata"] = {}
        return d
    finally:
        conn.close()


# =============================================================================
# STARTUP SEQUENCE
# =============================================================================

def _startup():
    """Initialize database, schemas, and seed demo users."""
    init_db()
    _init_mpesa_schema()

    # Seed demo users
    if not db.get_user_by_phone("+254700000000"):
        priv, pub = generate_keypair()
        db.create_user("+254700000000", "Demo User",   hash_password("1234"),  pub, priv, role="user")
        logger.info("Seeded demo user: +254700000000 / PIN 1234")

    if not db.get_user_by_phone("+254700000001"):
        priv, pub = generate_keypair()
        db.create_user("+254700000001", "Alice Kamau", hash_password("5678"),  pub, priv, role="user")
        logger.info("Seeded demo user: +254700000001 / PIN 5678")

    if not db.get_user_by_phone("+254700000099"):
        priv, pub = generate_keypair()
        db.create_user("+254700000099", "Admin User",  hash_password("admin123"), pub, priv, role="admin")
        logger.info("Seeded admin user: +254700000099 / PIN admin123")


_startup()


# =============================================================================
# FASTAPI APP
# =============================================================================

async def _expire_loop():
    """Background coroutine: expire stale M-Pesa pending deposits every 60s."""
    while True:
        await asyncio.sleep(60)
        try:
            _mpesa_expire_stale()
        except Exception as e:
            logger.error(f"expire_loop error: {e}")


@asynccontextmanager
async def lifespan(application: FastAPI):
    task = asyncio.create_task(_expire_loop())
    logger.info("ChainPay server started. Background expiry task running.")
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(
    title="ChainPay API",
    version="3.0.0",
    description="Blockchain-powered mobile money with M-Pesa — REST API",
    lifespan=lifespan,
)

bearer = HTTPBearer(auto_error=True)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],     # Restrict to your domain in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =============================================================================
# AUTH DEPENDENCIES
# =============================================================================

def require_auth(
    creds: HTTPAuthorizationCredentials = Depends(bearer)
) -> dict:
    sm      = get_session_manager()
    payload = sm.verify_token(creds.credentials)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    user = db.get_user_by_id(payload["sub"])
    if not user or user.get("is_suspended"):
        raise HTTPException(status_code=403, detail="Account suspended or not found")
    return payload


def require_admin(payload: dict = Depends(require_auth)) -> dict:
    if payload.get("role") not in ("admin", "compliance"):
        raise HTTPException(status_code=403, detail="Admin access required")
    return payload


def require_strict_admin(payload: dict = Depends(require_auth)) -> dict:
    if payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return payload


def _get_token_payload(request: Request) -> Optional[dict]:
    """Helper to extract and verify JWT from Authorization header."""
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return None
    token = auth_header.split(" ", 1)[1]
    sm = get_session_manager()
    return sm.verify_token(token)


# =============================================================================
# REQUEST / RESPONSE MODELS
# =============================================================================

WEAK_PINS = {
    "0000","1111","2222","3333","4444","5555",
    "6666","7777","8888","9999","1234","4321",
    "0123","9876","1122","2211","1212"
}


# ── Auth Models ───────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    phone: str
    pin: str

    @field_validator("pin")
    @classmethod
    def pin_not_empty(cls, v):
        if not v or len(v) < 4:
            raise ValueError("PIN must be at least 4 digits")
        return v

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v):
        if not v.startswith('+'):
            raise ValueError("Phone must be in E.164 format (e.g., +254...)")
        return v


class LoginResponse(BaseModel):
    token: str
    user: Dict   # includes first_login_completed, base_currency


class RegisterRequest(BaseModel):
    phone: str
    name: str
    pin: str

    @field_validator("pin")
    @classmethod
    def validate_pin(cls, v):
        if not v or len(v) < 4:
            raise ValueError("PIN must be at least 4 digits")
        if not v.isdigit():
            raise ValueError("PIN must contain only digits")
        return v

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v):
        if not v.startswith('+'):
            raise ValueError("Phone must be in E.164 format (e.g., +254...)")
        return v


class RegisterResponse(BaseModel):
    message: str
    user_id: str


class ChangePinRequest(BaseModel):
    old_pin: str
    new_pin: str

    @field_validator("new_pin")
    @classmethod
    def validate_new_pin(cls, v):
        if not v or len(v) < 4:
            raise ValueError("PIN must be at least 4 digits")
        if not v.isdigit():
            raise ValueError("PIN must contain only digits")
        return v


class ChangePinResponse(BaseModel):
    message: str


# ── Wallet Models ─────────────────────────────────────────────────────────────

class SendRequest(BaseModel):
    recipient_phone: str
    amount: float
    currency: str
    note: Optional[str] = ""
    pin: str

    @field_validator("amount")
    @classmethod
    def amount_positive(cls, v):
        if v <= 0:
            raise ValueError("Amount must be positive")
        return v

    @field_validator("pin")
    @classmethod
    def pin_required(cls, v):
        if not v or len(v) < 4:
            raise ValueError("PIN must be at least 4 digits")
        return v


class SendResponse(BaseModel):
    message: str
    transaction: Optional[Dict] = None


class ConvertRequest(BaseModel):
    from_currency: str
    to_currency: str
    amount: float

    @field_validator("amount")
    @classmethod
    def amount_positive(cls, v):
        if v <= 0:
            raise ValueError("Amount must be positive")
        return v


class ConvertResponse(BaseModel):
    message: str
    conversion: Optional[Dict] = None


class DepositRequest(BaseModel):
    amount: float
    currency: str

    @field_validator("amount")
    @classmethod
    def amount_positive(cls, v):
        if v <= 0:
            raise ValueError("Amount must be positive")
        return v


class DepositResponse(BaseModel):
    message: str
    transaction: Optional[Dict] = None


class WithdrawRequest(BaseModel):
    amount: float
    currency: str

    @field_validator("amount")
    @classmethod
    def amount_positive(cls, v):
        if v <= 0:
            raise ValueError("Amount must be positive")
        return v


class WithdrawResponse(BaseModel):
    message: str
    transaction: Optional[Dict] = None


class BalancesResponse(BaseModel):
    wallets: List[Dict]


class TransactionsResponse(BaseModel):
    transactions: List[Dict]


# ── FX Models ─────────────────────────────────────────────────────────────────

class FXQuoteResponse(BaseModel):
    from_currency: str
    to_currency: str
    from_amount: float
    to_amount: float
    mid_rate: float
    effective_rate: float
    spread_pct: float
    fx_fee: float
    valid_for_seconds: int


class FXRatesResponse(BaseModel):
    rates: List[Dict]


# ── Blockchain Models ─────────────────────────────────────────────────────────

class BlockchainStatsResponse(BaseModel):
    total_blocks: int
    total_transactions: int
    pending_transactions: int
    chain_valid: bool
    latest_block_hash: str
    latest_block_time: float


class BlockchainBlocksResponse(BaseModel):
    blocks: List[Dict]


class BlockchainValidateResponse(BaseModel):
    valid: bool
    message: str


class BlockchainMineResponse(BaseModel):
    message: str
    block: Optional[Dict] = None


# ── User Models ───────────────────────────────────────────────────────────────

class UserResponse(BaseModel):
    user_id: str
    name: str
    phone: str
    kyc_status: str


# ── M-Pesa Models ─────────────────────────────────────────────────────────────

class MpesaInitiateRequest(BaseModel):
    phone: str
    amount: float

    @field_validator("amount")
    @classmethod
    def amount_positive(cls, v):
        if v <= 0:
            raise ValueError("Amount must be positive")
        return v


class MpesaInitiateResponse(BaseModel):
    message: str
    internal_ref: str
    customer_message: str


class MpesaStatusResponse(BaseModel):
    status: str
    receipt_number: Optional[str] = None
    result_desc: Optional[str] = None


class MpesaHistoryResponse(BaseModel):
    deposits: List[Dict]


# ── Reversal Models ───────────────────────────────────────────────────────────

class ReversalEligibleResponse(BaseModel):
    eligible_transactions: List[Dict]


class ReversalRequest(BaseModel):
    tx_id: str
    reason: str = ""


class ReversalRequestResponse(BaseModel):
    message: str
    reversal_id: str


class AdminReversalsResponse(BaseModel):
    reversals: List[Dict]


class AdminReversalActionRequest(BaseModel):
    note: str = ""


class AdminReversalActionResponse(BaseModel):
    message: str


# ── Admin Models ──────────────────────────────────────────────────────────────

class SetRoleRequest(BaseModel):
    role: str


class AdminStatsResponse(BaseModel):
    total_users: int
    active_users: int
    suspended_users: int
    total_transactions: int
    failed_transactions: int
    total_volume_usd: float
    total_revenue: float
    suspicious_flags: int
    failed_logins_24h: int
    total_logins_24h: int
    db_size_kb: float


class AdminUsersResponse(BaseModel):
    users: List[Dict]


class AdminSuspiciousResponse(BaseModel):
    flags: List[Dict]


class AdminLoginAttemptsResponse(BaseModel):
    attempts: List[Dict]


class AdminAuditLogResponse(BaseModel):
    log: List[Dict]


class AdminConfigResponse(BaseModel):
    config: List[Dict]


class AdminTxStatsResponse(BaseModel):
    total_transactions: int
    failed_transactions: int
    total_revenue: float
    by_type: List[Dict]
    by_currency: List[Dict]


class AdminSystemBalancesResponse(BaseModel):
    balances: Dict[str, float]


class AdminBlockchainTxsResponse(BaseModel):
    transactions: List[Dict]


# ── Notification Models ───────────────────────────────────────────────────────

class NotificationsResponse(BaseModel):
    notifications: List[Dict]


class NotificationReadResponse(BaseModel):
    message: str


# ── Help Models ───────────────────────────────────────────────────────────────

class HelpSection(BaseModel):
    title: str
    icon: str
    steps: Optional[List[str]] = None
    tips: Optional[List[str]] = None
    content: Optional[str] = None
    note: Optional[str] = None


class HelpResponse(BaseModel):
    sections: List[HelpSection]


# =============================================================================
# HEALTH
# =============================================================================

@app.get("/health")
def health():
    return {
        "status":  "ok",
        "version": "3.0.0",
        "time":    time.time(),
        "mpesa_env": MPESA_CONFIG["environment"],
    }


# =============================================================================
# AUTH ROUTES
# =============================================================================

@app.post("/api/v1/auth/register", response_model=RegisterResponse)
async def register(req: RegisterRequest, request: Request):
    if db.get_user_by_phone(req.phone):
        raise HTTPException(400, "Phone number already registered")
    priv, pub = generate_keypair()
    pin_hash  = hash_password(req.pin)
    # create_user now initialises ALL currency wallets and sets first_login_completed=0
    user_id   = db.create_user(req.phone, req.name, pin_hash, pub, priv, role="user")
    return {"message": "Account created successfully", "user_id": user_id}


@app.post("/api/v1/auth/login", response_model=LoginResponse)
async def login(req: LoginRequest, request: Request):
    sm    = get_session_manager()
    ip    = request.client.host if request.client else ""
    phone = req.phone.strip()

    max_attempts = int(db.get_config("max_failed_login") or "5")
    lockout_secs = int(db.get_config("lockout_seconds")  or "300")
    failed_count = db.get_failed_login_count(phone, window_seconds=lockout_secs)

    if failed_count >= max_attempts:
        db.record_login_attempt(phone, success=False, ip_hash=ip)
        raise HTTPException(
            status_code=429,
            detail=(f"Account locked after {max_attempts} failed attempts. "
                    f"Try again in {lockout_secs // 60} minutes.")
        )

    user = db.get_user_by_phone(phone)

    if not user or not verify_password(req.pin, user["pin_hash"]):
        db.record_login_attempt(phone, success=False, ip_hash=ip)
        raise HTTPException(status_code=401, detail="Invalid phone number or PIN")

    if user.get("is_suspended"):
        db.record_login_attempt(phone, success=False, ip_hash=ip)
        raise HTTPException(status_code=403, detail="Account suspended. Contact support.")

    db.record_login_attempt(phone, success=True, ip_hash=ip)
    db.update_last_login(user["user_id"])
    sm.clear_failed_attempts(phone)

    # Back-fill any missing currency wallets for existing users (migration safety)
    db.ensure_all_currency_wallets(user["user_id"])

    # Determine whether to show the first-login deposit popup
    first_login_completed = db.get_first_login_completed(user["user_id"])

    token = sm.create_token(user["user_id"], phone, user.get("role_id", "user"))

    return {
        "token": token,
        "user": {
            "user_id":               user["user_id"],
            "name":                  user["name"],
            "phone":                 user["phone"],
            "role":                  user.get("role_id", "user"),
            "kyc_status":            user.get("kyc_status", "VERIFIED"),
            "base_currency":         user.get("base_currency", "USD"),
            "first_login_completed": first_login_completed,
        }
    }


@app.post("/api/v1/auth/first-login-done")
def mark_first_login_done(payload: dict = Depends(require_auth)):
    """
    Call this endpoint after the user closes or completes the first-login
    deposit popup. Sets first_login_completed=True so the popup never
    appears again on subsequent logins.
    """
    db.set_first_login_completed(payload["sub"])
    db.audit_action(payload["sub"], "FIRST_LOGIN_COMPLETED", {})
    return {"message": "First-login popup dismissed."}


@app.post("/api/v1/auth/change-pin")
def change_pin(req: ChangePinRequest, payload: dict = Depends(require_auth)):
    user = db.get_user_by_id(payload["sub"])
    if not user:
        raise HTTPException(404, "User not found")
    if not verify_password(req.old_pin, user["pin_hash"]):
        db.record_login_attempt(user["phone"], success=False)
        raise HTTPException(401, "Current PIN is incorrect")
    if req.old_pin == req.new_pin:
        raise HTTPException(400, "New PIN must be different from current PIN")
    new_hash = hash_password(req.new_pin)
    db.update_pin(payload["sub"], new_hash)
    db.audit_action(payload["sub"], "PIN_CHANGED", {})
    return {"message": "PIN changed successfully"}


# =============================================================================
# WALLET ROUTES
# =============================================================================

@app.get("/api/v1/wallet/balances")
def get_balances(payload: dict = Depends(require_auth)):
    wallets = db.get_all_wallets(payload["sub"])
    return {"wallets": wallets}


@app.post("/api/v1/wallet/send", response_model=SendResponse)
async def send_money(req: SendRequest, payload: dict = Depends(require_auth)):
    """Send money to another user. Requires PIN verification."""
    user_id = payload["sub"]

    # Verify PIN
    user = db.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if not verify_password(req.pin, user["pin_hash"]):
        db.audit_action(user_id, "SEND_FAILED", {
            "reason": "Invalid PIN",
            "recipient": req.recipient_phone,
            "amount": req.amount
        })
        raise HTTPException(status_code=401, detail="Invalid PIN")

    # Get recipient
    recipient = db.get_user_by_phone(req.recipient_phone)
    if not recipient:
        raise HTTPException(status_code=404, detail="Recipient not found")

    # Execute transfer
    success, message, tx_data = WalletService.send_money(
        sender_id=user_id,
        recipient_phone=req.recipient_phone,
        amount=req.amount,
        currency=req.currency,
        note=req.note or ""
    )

    if not success:
        raise HTTPException(status_code=400, detail=message)

    # Log success
    db.audit_action(user_id, "SEND_SUCCESS", {
        "tx_id": tx_data["tx_id"],
        "amount": req.amount,
        "currency": req.currency,
        "recipient": req.recipient_phone
    })

    return {
        "message": message,
        "transaction": tx_data
    }


@app.post("/api/v1/wallet/convert")
def convert_currency(req: ConvertRequest, payload: dict = Depends(require_auth)):
    if req.from_currency.upper() == req.to_currency.upper():
        raise HTTPException(400, "Cannot convert to the same currency")
    ok, msg, data = WalletService.convert_currency(
        payload["sub"],
        req.from_currency.upper(),
        req.to_currency.upper(),
        req.amount
    )
    if not ok:
        raise HTTPException(400, msg)
    return {"message": msg, "conversion": data}


@app.post("/api/v1/wallet/deposit")
def deposit(req: DepositRequest, payload: dict = Depends(require_auth)):
    """Manual deposit (not M-Pesa). For testing or internal use."""
    ok, msg, data = WalletService.deposit(
        payload["sub"], req.amount, req.currency.upper()
    )
    if not ok:
        raise HTTPException(400, msg)
    return {"message": msg, "transaction": data}


@app.post("/api/v1/wallet/withdraw")
def withdraw(req: WithdrawRequest, payload: dict = Depends(require_auth)):
    ok, msg, data = WalletService.withdraw(
        payload["sub"], req.amount, req.currency.upper()
    )
    if not ok:
        raise HTTPException(400, msg)
    return {"message": msg, "transaction": data}


@app.get("/api/v1/wallet/transactions")
def get_transactions(
    limit: int = Query(default=50, ge=1, le=200),
    payload: dict = Depends(require_auth)
):
    txs = db.get_user_transactions(payload["sub"], limit=limit)
    return {"transactions": txs}


# =============================================================================
# FX ROUTES
# =============================================================================

@app.get("/api/v1/fx/rates")
def get_fx_rates():
    rates = FXEngine.get_rate_table()
    return {"rates": rates}


@app.get("/api/v1/fx/quote")
def get_fx_quote(
    from_ccy: str   = Query(..., alias="from"),
    to_ccy:   str   = Query(..., alias="to"),
    amount:   float = Query(..., gt=0)
):
    quote = FXEngine.get_conversion_quote(from_ccy.upper(), to_ccy.upper(), amount)
    if not quote:
        raise HTTPException(400, f"FX rate not available for {from_ccy}/{to_ccy}")
    return quote


# =============================================================================
# BLOCKCHAIN ROUTES
# =============================================================================

@app.get("/api/v1/blockchain/stats")
def blockchain_stats(payload: dict = Depends(require_auth)):
    return get_blockchain().get_chain_stats()


@app.get("/api/v1/blockchain/blocks")
def blockchain_blocks(
    n: int = Query(default=20, ge=1, le=100),
    payload: dict = Depends(require_auth)
):
    return {"blocks": get_blockchain().get_recent_blocks(n)}


@app.post("/api/v1/blockchain/mine")
def mine_block(payload: dict = Depends(require_auth)):
    block = get_blockchain().mine_block(force=True)
    if block:
        return {
            "message":     "Block mined",
            "block_index": block.index,
            "block_hash":  block.block_hash[:32] + "...",
            "tx_count":    len(block.transactions),
        }
    return {"message": "No pending transactions to mine"}


@app.get("/api/v1/blockchain/validate")
def validate_chain(payload: dict = Depends(require_auth)):
    valid, msg = get_blockchain().validate_chain()
    return {"valid": valid, "message": msg}


# =============================================================================
# USER ROUTES
# =============================================================================

@app.get("/api/v1/user/by-phone/{phone}", response_model=UserResponse)
async def get_user_by_phone(
    phone: str,
    payload: dict = Depends(require_auth)
):
    """
    Get user information by phone number.
    Used by client to display recipient name before transfer.
    """
    user = db.get_user_by_phone(phone)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return {
        "user_id":    user["user_id"],
        "name":       user["name"],
        "phone":      user["phone"],
        "kyc_status": user.get("kyc_status", "PENDING")
    }


# =============================================================================
# M-PESA ROUTES
# =============================================================================

@app.post("/api/v1/mpesa/initiate", response_model=MpesaInitiateResponse)
async def mpesa_initiate(req: MpesaInitiateRequest, payload: dict = Depends(require_auth)):
    """Initiate M-Pesa STK Push deposit."""
    user_id = payload["sub"]

    # Validate phone number
    try:
        normalized_phone = _validate_kenyan_phone(req.phone)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Create pending record
    pending = _mpesa_create_pending(user_id, normalized_phone, req.amount)

    # Initiate STK Push
    try:
        result = _initiate_stk_push(
            phone=normalized_phone,
            amount=int(req.amount),
            internal_ref=pending["internal_ref"]
        )

        # Store checkout request ID
        _mpesa_set_checkout_id(
            pending["internal_ref"],
            result["CheckoutRequestID"],
            result.get("MerchantRequestID", "")
        )

        return MpesaInitiateResponse(
            message="STK Push sent successfully",
            internal_ref=pending["internal_ref"],
            customer_message=result.get("CustomerMessage", "Please check your phone and enter PIN")
        )
    except RuntimeError as e:
        logger.error(f"M-Pesa initiation failed: {e}")
        raise HTTPException(status_code=500, detail=f"STK Push failed: {str(e)}")


@app.get("/api/v1/mpesa/status/{internal_ref}", response_model=MpesaStatusResponse)
async def mpesa_status(internal_ref: str, payload: dict = Depends(require_auth)):
    """Get M-Pesa deposit status."""
    tx = _mpesa_get_by_ref(internal_ref, payload["sub"])
    if not tx:
        raise HTTPException(status_code=404, detail="Transaction not found")

    return MpesaStatusResponse(
        status=tx["status"],
        receipt_number=tx.get("mpesa_receipt"),
        result_desc=tx.get("result_desc")
    )


@app.get("/api/v1/mpesa/history", response_model=MpesaHistoryResponse)
async def mpesa_history(
    limit: int = Query(default=50, ge=1, le=200),
    payload: dict = Depends(require_auth)
):
    """Get user's M-Pesa deposit history."""
    deposits = _mpesa_user_history(payload["sub"], limit)
    for d in deposits:
        d["amount_kes"] = d["amount_kes"] / 100.0
    return MpesaHistoryResponse(deposits=deposits)


@app.post("/api/v1/mpesa/callback")
async def mpesa_callback(request: Request):
    """M-Pesa STK Push callback endpoint."""
    try:
        body   = await request.json()
        logger.info(f"M-Pesa callback received: {json.dumps(body)[:200]}...")
        result = _parse_mpesa_callback(body)

        if result["success"]:
            tx = _mpesa_get_by_checkout(result["checkout_request_id"])
            if tx and tx["status"] == "PENDING":
                chainpay_tx_id = str(uuid.uuid4())
                conn = _mpesa_conn()
                try:
                    conn.execute("BEGIN IMMEDIATE")

                    # Credit user's KES wallet
                    conn.execute(
                        "UPDATE wallets SET balance = balance + ? WHERE user_id = ? AND currency = 'KES'",
                        (int(result["amount"] * 100), tx["user_id"])
                    )

                    # Record transaction
                    conn.execute(
                        """INSERT INTO transactions
                           (tx_id, sender, recipient, amount, currency, tx_type, fee, timestamp, status, metadata)
                           VALUES (?, 'MPESA', ?, ?, 'KES', 'MPESA_DEPOSIT', 0, ?, 'CONFIRMED', ?)""",
                        (chainpay_tx_id, tx["user_id"], int(result["amount"] * 100),
                         time.time(),
                         json.dumps({
                             "mpesa_receipt":      result["receipt_number"],
                             "phone":              result["phone"],
                             "checkout_request_id": result["checkout_request_id"]
                         }))
                    )

                    # Update M-Pesa transaction record
                    conn.execute(
                        """UPDATE mpesa_transactions SET
                           status='CONFIRMED', result_code=?, result_desc=?,
                           mpesa_receipt=?, mpesa_phone=?, wallet_credited=1,
                           chainpay_tx_id=?, callback_at=?, raw_callback=?
                           WHERE checkout_request_id=?""",
                        (result["result_code"], result["result_desc"],
                         result["receipt_number"], result["phone"],
                         chainpay_tx_id, time.time(), json.dumps(body),
                         result["checkout_request_id"])
                    )

                    conn.commit()
                    logger.info(f"Wallet credited: {chainpay_tx_id} for KES {result['amount']}")

                except Exception as e:
                    conn.rollback()
                    logger.error(f"Failed to credit wallet: {e}")
                finally:
                    conn.close()
            else:
                logger.warning(
                    f"Duplicate callback or expired transaction: {result['checkout_request_id']}"
                )
        else:
            # Handle failed transaction
            tx = _mpesa_get_by_checkout(result["checkout_request_id"])
            if tx:
                _mpesa_fail(
                    tx["internal_ref"],
                    result["result_code"],
                    result["result_desc"],
                    body
                )

        return {"ResultCode": 0, "ResultDesc": "Success"}

    except Exception as e:
        logger.error(f"Callback processing error: {e}")
        return {"ResultCode": 1, "ResultDesc": "Error processing callback"}


# =============================================================================
# REVERSAL ROUTES
# =============================================================================

@app.get("/api/v1/reversal/eligible")
def reversal_eligible(payload: dict = Depends(require_auth)):
    """
    Returns transfers eligible for reversal:
    - Type = SEND, Status = CONFIRMED
    - Within last 24 hours
    - No existing pending/approved reversal
    """
    user_id = payload["sub"]
    since   = time.time() - 86400
    txs     = db.get_user_transactions(user_id, limit=200)

    eligible = []
    conn = _mpesa_conn()
    try:
        for tx in txs:
            if (tx["tx_type"] == "SEND"
                    and tx["status"] == "CONFIRMED"
                    and tx["sender"] == user_id
                    and tx["timestamp"] >= since):
                existing = conn.execute(
                    "SELECT reversal_id FROM reversal_requests "
                    "WHERE tx_id=? AND status IN ('PENDING','APPROVED','COMPLETED')",
                    (tx["tx_id"],)
                ).fetchone()
                if not existing:
                    eligible.append({
                        "tx_id":     tx["tx_id"],
                        "amount":    tx["amount"],
                        "currency":  tx["currency"],
                        "recipient": tx.get("metadata", {}).get("recipient_phone", tx["recipient"]),
                        "timestamp": tx["timestamp"],
                        "note":      tx.get("metadata", {}).get("note", ""),
                    })
    finally:
        conn.close()

    return {"eligible_transactions": eligible}


@app.post("/api/v1/reversal/request")
def request_reversal(
    req: ReversalRequest,
    payload: dict = Depends(require_auth),
):
    """
    User requests a reversal of a recent SEND transaction.
    Creates a PENDING reversal_request record for admin review.
    Does NOT touch balances — admin approves before any money moves.
    """
    user_id = payload["sub"]
    tx      = _get_tx_by_id(req.tx_id)

    if not tx:
        raise HTTPException(404, "Transaction not found")
    if tx["sender"] != user_id:
        raise HTTPException(403, "You can only reverse your own transactions")
    if tx["tx_type"] != "SEND":
        raise HTTPException(400, "Only SEND transactions can be reversed")
    if tx["status"] != "CONFIRMED":
        raise HTTPException(400, "Only confirmed transactions can be reversed")
    if time.time() - tx["timestamp"] > 86400:
        raise HTTPException(400, "Reversal window expired (must be within 24 hours)")

    try:
        reversal = _reversal_create(req.tx_id, user_id, req.reason)
    except ValueError as e:
        raise HTTPException(409, str(e))

    db.audit_action(user_id, "REVERSAL_REQUESTED", {
        "tx_id":       req.tx_id,
        "reversal_id": reversal["reversal_id"],
        "reason":      req.reason,
    })

    return {
        "message":     "Reversal request submitted. An admin will review it shortly.",
        "reversal_id": reversal["reversal_id"],
    }


# =============================================================================
# HELP PANEL ROUTE
# =============================================================================

@app.get("/api/v1/help")
def get_help():
    """Returns structured help content for the in-app Help Panel. Public endpoint."""
    return {
        "sections": [
            {
                "title": "How to Deposit via M-Pesa",
                "icon":  "📱",
                "steps": [
                    "Tap 'Deposit via M-Pesa' on your dashboard.",
                    "Enter or confirm your M-Pesa phone number (the one registered with Safaricom).",
                    "Enter the amount in KES you wish to deposit (minimum KES 1, maximum KES 150,000).",
                    "Tap 'Send M-Pesa STK Push'.",
                    "A payment prompt will appear on your phone — enter your M-Pesa PIN.",
                    "Wait a moment. Your ChainPay wallet is credited automatically once Safaricom confirms.",
                    "You will receive an M-Pesa SMS confirmation and see the balance update on screen.",
                ],
                "note": "Your wallet is ONLY credited after Safaricom confirms payment. Never before."
            },
            {
                "title": "How to Send Money",
                "icon":  "💸",
                "steps": [
                    "Tap 'Send Money' on your dashboard.",
                    "Enter the recipient's phone number (must be a registered ChainPay user).",
                    "Enter the amount and select the currency.",
                    "A confirmation screen shows: recipient name, amount, fee, and total deduction.",
                    "Verify the recipient name carefully before proceeding.",
                    "Tap 'Confirm' to execute. Recipient is credited instantly.",
                ],
                "note": "Always verify the recipient's name on the confirmation screen."
            },
            {
                "title": "How Currency Conversion Works",
                "icon":  "💱",
                "steps": [
                    "Your base currency is KES.",
                    "Go to 'Convert Currency' and select your target currency.",
                    "The current rate and a 1.5% spread fee are displayed.",
                    "Confirm — KES is debited and the target currency is credited atomically.",
                    "Rates refresh every 30 seconds.",
                ],
                "note": "Currency conversions are final and cannot be reversed."
            },
            {
                "title": "Wrong Transfer? Request a Reversal",
                "icon":  "⚠️",
                "steps": [
                    "Act fast — reversals are only possible within 24 hours of the transfer.",
                    "Go to your transaction history and find the transfer.",
                    "Tap 'Request Reversal' and provide a brief reason.",
                    "An admin will review the request and may contact both parties.",
                    "If approved, the amount is returned to your wallet.",
                ],
                "note": "Reversals are NOT guaranteed. Always double-check before sending."
            },
            {
                "title": "Reversal Process Explained",
                "icon":  "🔄",
                "content": (
                    "Reversals are admin-controlled for security. "
                    "Once submitted, the request enters a review queue. "
                    "The admin verifies both sides and — if approved — "
                    "atomically returns the funds to your wallet and deducts from the recipient. "
                    "You will be notified of the outcome."
                )
            },
            {
                "title": "Contact Support",
                "icon":  "🆘",
                "content": (
                    "For urgent issues: support@chainpay.app. "
                    "For M-Pesa disputes, contact Safaricom directly via *234# "
                    "or call 0722 000 000."
                )
            },
            {
                "title": "Security Best Practices",
                "icon":  "🔐",
                "tips": [
                    "Never share your PIN with anyone, including ChainPay staff.",
                    "Always log out after using ChainPay on a shared device.",
                    "Enable your phone's screen lock to protect your M-Pesa.",
                    "Always verify the recipient's name before confirming any transfer.",
                    "If you suspect unauthorized access, change your PIN immediately.",
                    "ChainPay will NEVER ask for your M-Pesa PIN via SMS, call, or email.",
                ]
            },
        ]
    }


# =============================================================================
# ADMIN ROUTES
# =============================================================================

@app.get("/api/v1/admin/stats")
def admin_stats(payload: dict = Depends(require_admin)):
    return db.get_system_stats()


@app.get("/api/v1/admin/tx-stats")
def admin_tx_stats(payload: dict = Depends(require_admin)):
    return db.get_tx_stats()


@app.get("/api/v1/admin/system-balances")
def admin_system_balances(payload: dict = Depends(require_admin)):
    return db.get_total_system_balances()


@app.get("/api/v1/admin/users")
def admin_get_users(payload: dict = Depends(require_admin)):
    return {"users": db.get_all_users()}


@app.post("/api/v1/admin/users/{user_id}/suspend")
def admin_suspend(
    user_id: str = FPath(...),
    payload: dict = Depends(require_strict_admin)
):
    if user_id == payload["sub"]:
        raise HTTPException(400, "Cannot suspend yourself")
    db.suspend_user(user_id, payload["sub"], "Admin action via API")
    return {"message": "User suspended"}


@app.post("/api/v1/admin/users/{user_id}/unsuspend")
def admin_unsuspend(
    user_id: str = FPath(...),
    payload: dict = Depends(require_strict_admin)
):
    db.unsuspend_user(user_id, payload["sub"])
    return {"message": "User unsuspended"}


@app.post("/api/v1/admin/users/{user_id}/role")
def admin_set_role(
    req: SetRoleRequest,
    user_id: str = FPath(...),
    payload: dict = Depends(require_strict_admin)
):
    if user_id == payload["sub"] and req.role != "admin":
        raise HTTPException(400, "Cannot demote yourself")
    db.set_user_role(user_id, req.role, payload["sub"])
    return {"message": f"Role updated to {req.role}"}


@app.get("/api/v1/admin/suspicious")
def admin_suspicious(payload: dict = Depends(require_admin)):
    return {"flags": db.get_suspicious_activity(resolved=False)}


@app.post("/api/v1/admin/flags/{flag_id}/resolve")
def admin_resolve_flag(
    flag_id: str = FPath(...),
    payload: dict = Depends(require_admin)
):
    db.resolve_suspicious_flag(flag_id, payload["sub"])
    return {"message": "Flag resolved"}


@app.get("/api/v1/admin/login-attempts")
def admin_login_attempts(
    limit: int = Query(default=100, ge=1, le=500),
    payload: dict = Depends(require_admin)
):
    return {"attempts": db.get_login_attempts(limit=limit)}


@app.get("/api/v1/admin/audit-log")
def admin_audit_log(
    limit: int = Query(default=100, ge=1, le=500),
    payload: dict = Depends(require_admin)
):
    return {"log": db.get_audit_log(limit=limit)}


@app.get("/api/v1/admin/config")
def admin_get_config(payload: dict = Depends(require_admin)):
    return {"config": db.get_all_config()}


@app.get("/api/v1/admin/blockchain-txs")
def admin_blockchain_txs(
    limit: int = Query(default=30, ge=1, le=200),
    payload: dict = Depends(require_admin)
):
    bc  = get_blockchain()
    txs = list(reversed(list(bc.tx_index.values())[-limit:]))
    return {"transactions": txs}


# ── Admin: M-Pesa transaction monitoring ──────────────────────────────────────

@app.get("/api/v1/admin/mpesa-transactions")
def admin_mpesa_txs(
    status: Optional[str] = Query(default=None),
    limit:  int           = Query(default=200, ge=1, le=500),
    payload: dict = Depends(require_admin)
):
    """All M-Pesa deposit attempts with full status history."""
    records = _mpesa_all(status=status, limit=limit)
    for r in records:
        r["amount_kes"] = r["amount_kes"] / 100.0
    return {"transactions": records}


# ── Admin: Reversal management ────────────────────────────────────────────────

@app.get("/api/v1/admin/reversals")
def admin_reversals(
    include_history: bool = Query(default=False),
    payload: dict = Depends(require_admin)
):
    """List pending reversal requests (or full history with include_history=true)."""
    if include_history:
        return {"reversals": _reversal_history()}
    return {"reversals": _reversal_pending_list()}


@app.post("/api/v1/admin/reversals/{reversal_id}/approve")
def admin_approve_reversal(
    reversal_id: str,
    req: AdminReversalActionRequest,
    payload: dict = Depends(require_admin),
):
    """
    Approve a reversal:
    1. Verify reversal exists and is PENDING
    2. Atomic balance swap: debit recipient → credit sender
    3. Mark original transaction REVERSED
    4. Insert REVERSAL transaction record
    5. Complete reversal record
    """
    admin_id = payload["sub"]
    reversal = _reversal_get(reversal_id)

    if not reversal:
        raise HTTPException(404, "Reversal request not found")
    if reversal["status"] != "PENDING":
        raise HTTPException(400, f"Reversal is already {reversal['status']}")

    tx = _get_tx_by_id(reversal["tx_id"])
    if not tx:
        raise HTTPException(404, "Original transaction not found")

    currency     = tx["currency"]
    sender       = tx["sender"]
    recipient    = tx["recipient"]
    amount_minor = int(round(tx["amount"] * 100)) if isinstance(tx["amount"], float) else tx["amount"]

    try:
        with db.get_db() as conn:
            # Debit recipient (must have sufficient balance)
            if not db.update_balance(conn, recipient, currency, -amount_minor):
                raise HTTPException(
                    400,
                    f"Recipient has insufficient {currency} balance to reverse this transaction."
                )
            # Credit sender
            db.update_balance(conn, sender, currency, +amount_minor)
            # Mark original as REVERSED
            conn.execute(
                "UPDATE transactions SET status='REVERSED' WHERE tx_id=?",
                (tx["tx_id"],)
            )
            # Record reversal transaction
            rev_tx_id = str(uuid.uuid4())
            conn.execute(
                """INSERT INTO transactions
                   (tx_id, sender, recipient, amount, currency, tx_type,
                    fee, timestamp, status, metadata)
                   VALUES (?,?,?,?,?,'REVERSAL',0,?,'CONFIRMED',?)""",
                (rev_tx_id, recipient, sender, amount_minor, currency,
                 time.time(), json.dumps({
                     "original_tx_id": tx["tx_id"],
                     "reversal_id":    reversal_id,
                     "admin_note":     req.note,
                 }))
            )
            # Approve + complete reversal record
            now = time.time()
            conn.execute(
                """UPDATE reversal_requests SET
                   status='COMPLETED', admin_id=?, admin_note=?,
                   reviewed_at=?, completed_at=?
                   WHERE reversal_id=?""",
                (admin_id, req.note, now, now, reversal_id)
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Reversal execution error: {e}")
        raise HTTPException(500, f"Reversal execution failed: {e}")

    db.audit_action(admin_id, "REVERSAL_APPROVED", {
        "reversal_id": reversal_id, "tx_id": tx["tx_id"],
        "amount": tx["amount"], "currency": currency,
    })
    db.audit_action(sender, "REVERSAL_CREDITED", {
        "reversal_id": reversal_id, "amount": tx["amount"], "currency": currency,
    })

    logger.info(
        f"✅ Reversal APPROVED by {admin_id} | "
        f"reversal={reversal_id} | tx={tx['tx_id']} | "
        f"{currency} {tx['amount']:.2f} returned to sender"
    )

    return {
        "message":        "Reversal approved and balances updated.",
        "reversal_id":    reversal_id,
        "reversed_tx_id": tx["tx_id"],
        "new_tx_id":      rev_tx_id,
    }


@app.post("/api/v1/admin/reversals/{reversal_id}/reject")
def admin_reject_reversal(
    reversal_id: str,
    req: AdminReversalActionRequest,
    payload: dict = Depends(require_admin),
):
    """Reject a reversal request. No balance changes occur."""
    admin_id = payload["sub"]
    reversal = _reversal_get(reversal_id)

    if not reversal:
        raise HTTPException(404, "Reversal request not found")
    if reversal["status"] != "PENDING":
        raise HTTPException(400, f"Reversal is already {reversal['status']}")

    with db.get_db() as conn:
        conn.execute(
            """UPDATE reversal_requests SET
               status='REJECTED', admin_id=?, admin_note=?, reviewed_at=?
               WHERE reversal_id=?""",
            (admin_id, req.note, time.time(), reversal_id)
        )

    db.audit_action(admin_id, "REVERSAL_REJECTED", {
        "reversal_id": reversal_id, "note": req.note,
    })

    logger.info(f"Reversal REJECTED by {admin_id} | reversal={reversal_id}")

    return {"message": "Reversal request rejected.", "reversal_id": reversal_id}


# =============================================================================
# DATABASE PATCHING (backwards compat with older database.py)
# =============================================================================

def _patch_db():
    """
    Safely add any missing functions to the db module.
    Prevents crashes if database.py is an older version.
    """
    if not hasattr(db, "update_pin"):
        def update_pin(user_id: str, new_pin_hash: str):
            with db.get_db() as conn:
                conn.execute(
                    "UPDATE users SET pin_hash=? WHERE user_id=?",
                    (new_pin_hash, user_id)
                )
        db.update_pin = update_pin

    if not hasattr(db, "get_user_wallets"):
        db.get_user_wallets = db.get_all_wallets

    if not hasattr(db, "ensure_all_currency_wallets"):
        # Minimal back-fill for old db module: create wallets on-the-fly
        _SUPPORTED = ["USD", "EUR", "KES", "NGN", "GBP"]
        import uuid as _uuid, time as _time
        def _ensure(user_id: str):
            now = _time.time()
            with db.get_db() as conn:
                for ccy in _SUPPORTED:
                    conn.execute(
                        "INSERT OR IGNORE INTO wallets "
                        "(wallet_id, user_id, currency, balance, created_at) VALUES (?,?,?,0,?)",
                        (str(_uuid.uuid4()), user_id, ccy, now)
                    )
        db.ensure_all_currency_wallets = _ensure

    if not hasattr(db, "get_first_login_completed"):
        def _get_flc(user_id: str) -> bool:
            with db.get_db() as conn:
                try:
                    row = conn.execute(
                        "SELECT first_login_completed FROM users WHERE user_id=?", (user_id,)
                    ).fetchone()
                    return bool(row["first_login_completed"]) if row else True
                except Exception:
                    return True   # Column missing — assume done (don't spam popup)
        db.get_first_login_completed = _get_flc

    if not hasattr(db, "set_first_login_completed"):
        def _set_flc(user_id: str):
            with db.get_db() as conn:
                try:
                    conn.execute(
                        "UPDATE users SET first_login_completed=1 WHERE user_id=?", (user_id,)
                    )
                except Exception:
                    pass
        db.set_first_login_completed = _set_flc


_patch_db()


# =============================================================================
# ERROR HANDLERS
# =============================================================================

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catch-all: never leak stack traces to clients."""
    logger.exception(f"Unhandled exception on {request.url}: {exc}")
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error. Check server logs."}
    )


# =============================================================================
# TLS CERTIFICATE AUTO-GENERATION
# =============================================================================

def _ensure_certs():
    """Auto-generate a self-signed TLS certificate if none exists."""
    cert_dir  = os.path.join(os.path.dirname(__file__), "certs")
    key_file  = os.path.join(cert_dir, "key.pem")
    cert_file = os.path.join(cert_dir, "cert.pem")

    if os.path.exists(key_file) and os.path.exists(cert_file):
        try:
            import ssl
            ctx = ssl.create_default_context()
            ctx.load_cert_chain(cert_file, key_file)
            return key_file, cert_file
        except Exception:
            print("Certificate files exist but are invalid. Regenerating...")
            try:
                os.remove(key_file)
                os.remove(cert_file)
            except Exception:
                pass

    os.makedirs(cert_dir, exist_ok=True)
    print("Generating self-signed TLS certificate...")

    try:
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        import datetime as dt
        from datetime import timezone
        import ipaddress

        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "chainpay-server")])

        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(private_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(dt.datetime.now(timezone.utc))
            .not_valid_after(dt.datetime.now(timezone.utc) + dt.timedelta(days=365))
            .add_extension(
                x509.SubjectAlternativeName([
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
                    x509.IPAddress(ipaddress.IPv4Address("0.0.0.0")),
                ]),
                critical=False,
            )
            .sign(private_key, hashes.SHA256())
        )

        with open(key_file, "wb") as f:
            f.write(private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            ))
        with open(cert_file, "wb") as f:
            f.write(cert.public_bytes(serialization.Encoding.PEM))

        print(f"✓ Certificate generated: {cert_file}")
        return key_file, cert_file

    except Exception as e:
        print(f"WARNING: Certificate generation failed: {e}")
        print("Falling back to HTTP mode.")
        return None, None


# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    HOST     = "0.0.0.0"
    PORT     = 8443
    USE_HTTP = os.environ.get("CHAINPAY_HTTP", "").lower() in ("1", "true", "yes")

    print("\n" + "="*60)
    print("  ChainPay API Server v3.0 — M-Pesa Edition")
    print("="*60)
    print(f"  M-Pesa Environment : {MPESA_CONFIG['environment'].upper()}")
    print(f"  Shortcode          : {MPESA_CONFIG['shortcode']}")
    print(f"  Callback URL       : {MPESA_CONFIG['callback_url']}")
    print(f"  Consumer Key       : {MPESA_CONFIG['consumer_key'][:10]}...")
    print("="*60)

    if USE_HTTP:
        print(f"\n  Mode: HTTP (development)")
        print(f"  URL:  http://localhost:{PORT}")
        print(f"  Docs: http://localhost:{PORT}/docs\n")
        uvicorn.run("server:app", host=HOST, port=PORT,
                    reload=False, workers=1, log_level="info")
    else:
        key_file, cert_file = _ensure_certs()
        if key_file and cert_file:
            try:
                print(f"\n  Mode: HTTPS (self-signed cert)")
                print(f"  URL:  https://localhost:{PORT}")
                print(f"  Docs: https://localhost:{PORT}/docs\n")
                uvicorn.run("server:app", host=HOST, port=PORT,
                            ssl_keyfile=key_file, ssl_certfile=cert_file,
                            reload=False, workers=1, log_level="info")
            except Exception as e:
                print(f"\n⚠️  HTTPS failed: {e} — falling back to HTTP\n")
                uvicorn.run("server:app", host=HOST, port=PORT,
                            reload=False, workers=1, log_level="info")
        else:
            print(f"\n  Mode: HTTP (cert generation failed)")
            print(f"  URL:  http://localhost:{PORT}\n")
            uvicorn.run("server:app", host=HOST, port=PORT,
                        reload=False, workers=1, log_level="info")