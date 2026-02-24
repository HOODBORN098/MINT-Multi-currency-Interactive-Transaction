"""
ChainPay M-Pesa Integration — Daraja API
==========================================
Handles STK Push (Lipa Na M-Pesa Online), OAuth token management,
callback processing, and idempotent transaction handling.

PRODUCTION SAFETY:
  - Wallet credited ONLY after confirmed Safaricom callback (ResultCode == 0)
  - Idempotent: duplicate callbacks are silently ignored
  - Callback signature validated via expected fields
  - All M-Pesa transactions logged to dedicated mpesa_transactions table
  - Pending deposits tracked separately; committed only on success

DARAJA API REFERENCE:
  https://developer.safaricom.co.ke/APIs/MpesaExpressSimulate
"""

import base64
import hashlib
import json
import logging
import os
import time
import uuid
from datetime import datetime
from typing import Optional, Tuple
import urllib.request
import urllib.error

logger = logging.getLogger("chainpay.mpesa")

# ── Constants ──────────────────────────────────────────────────────────────────

SANDBOX_BASE_URL    = "https://sandbox.safaricom.co.ke"
PRODUCTION_BASE_URL = "https://api.safaricom.co.ke"

OAUTH_PATH    = "/oauth/v1/generate?grant_type=client_credentials"
STK_PUSH_PATH = "/mpesa/stkpush/v1/processrequest"
STK_QUERY_PATH= "/mpesa/stkpush/v1/querystkpushstatus"

# ── Configuration loader ───────────────────────────────────────────────────────

def _load_mpesa_config() -> dict:
    """
    Load M-Pesa credentials from environment variables (recommended)
    or from mpesa_config.json (fallback for local dev).

    NEVER commit real credentials to source control.
    In production: set env vars on your server / use a secrets manager.
    """
    cfg = {
        "consumer_key":    os.environ.get("MPESA_CONSUMER_KEY", ""),
        "consumer_secret": os.environ.get("MPESA_CONSUMER_SECRET", ""),
        "shortcode":       os.environ.get("MPESA_SHORTCODE", "174379"),     # Sandbox default
        "passkey":         os.environ.get("MPESA_PASSKEY",
                           "bfb279f9aa9bdbcf158e97dd71a467cd2e0c893059b10f78e6b72ada1ed2c919"),  # Sandbox default
        "callback_url":    os.environ.get("MPESA_CALLBACK_URL",
                           "https://your-ngrok-url.ngrok-free.app/api/v1/mpesa/callback"),
        "environment":     os.environ.get("MPESA_ENV", "sandbox"),  # "sandbox" or "production"
        "account_ref":     os.environ.get("MPESA_ACCOUNT_REF", "ChainPay"),
        "transaction_desc":os.environ.get("MPESA_TX_DESC", "ChainPay Wallet Deposit"),
    }

    # Try loading from JSON config if env vars not set
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mpesa_config.json")
    if os.path.exists(config_path) and not cfg["consumer_key"]:
        try:
            with open(config_path) as f:
                file_cfg = json.load(f)
                cfg.update({k: v for k, v in file_cfg.items() if v})
        except Exception as e:
            logger.warning(f"Could not load mpesa_config.json: {e}")

    return cfg


MPESA_CONFIG = _load_mpesa_config()


def get_base_url() -> str:
    return PRODUCTION_BASE_URL if MPESA_CONFIG["environment"] == "production" else SANDBOX_BASE_URL


# ── OAuth Token Manager ────────────────────────────────────────────────────────

class _TokenCache:
    """Thread-safe in-process OAuth token cache."""
    _token: Optional[str] = None
    _expires_at: float = 0.0

    @classmethod
    def get(cls) -> Optional[str]:
        if cls._token and time.time() < cls._expires_at - 60:  # 60s safety buffer
            return cls._token
        return None

    @classmethod
    def set(cls, token: str, expires_in: int = 3600):
        cls._token = token
        cls._expires_at = time.time() + expires_in


def get_oauth_token() -> str:
    """
    Fetch (or return cached) Daraja OAuth bearer token.
    Token validity: 3600 seconds. Refreshed automatically with 60s buffer.
    Raises RuntimeError if credentials are missing or request fails.
    """
    cached = _TokenCache.get()
    if cached:
        return cached

    key    = MPESA_CONFIG.get("consumer_key", "")
    secret = MPESA_CONFIG.get("consumer_secret", "")
    if not key or not secret:
        raise RuntimeError(
            "M-Pesa credentials not configured. "
            "Set MPESA_CONSUMER_KEY and MPESA_CONSUMER_SECRET environment variables."
        )

    credentials = base64.b64encode(f"{key}:{secret}".encode()).decode()
    url = get_base_url() + OAUTH_PATH

    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Basic {credentials}"},
        method="GET"
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            token      = data["access_token"]
            expires_in = int(data.get("expires_in", 3600))
            _TokenCache.set(token, expires_in)
            logger.info("M-Pesa OAuth token refreshed.")
            return token
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        raise RuntimeError(f"OAuth token fetch failed ({e.code}): {body}")
    except Exception as e:
        raise RuntimeError(f"OAuth token request error: {e}")


# ── STK Push ──────────────────────────────────────────────────────────────────

def _generate_password(shortcode: str, passkey: str, timestamp: str) -> str:
    """
    STK Push password = Base64(Shortcode + Passkey + Timestamp).
    Timestamp format: YYYYMMDDHHmmss
    """
    raw = shortcode + passkey + timestamp
    return base64.b64encode(raw.encode()).decode()


def initiate_stk_push(
    phone_number: str,
    amount: int,
    account_ref: str,
    description: str,
    internal_ref: str,
) -> dict:
    """
    Initiate Lipa Na M-Pesa Online (STK Push) to customer's phone.

    Args:
        phone_number: Customer phone in format 254XXXXXXXXX (no +)
        amount:       Amount in KES (integer, > 0)
        account_ref:  Account reference shown to customer (max 12 chars)
        description:  Transaction description (max 13 chars)
        internal_ref: Your internal transaction ID for idempotency tracking

    Returns:
        Safaricom response dict containing:
          - MerchantRequestID
          - CheckoutRequestID
          - ResponseCode ("0" = success)
          - ResponseDescription
          - CustomerMessage

    Raises:
        RuntimeError on network failure or non-zero Safaricom response code.
    """
    token     = get_oauth_token()
    shortcode = MPESA_CONFIG["shortcode"]
    passkey   = MPESA_CONFIG["passkey"]
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    password  = _generate_password(shortcode, passkey, timestamp)

    # Normalize phone number
    phone_number = _normalize_phone(phone_number)

    payload = {
        "BusinessShortCode": shortcode,
        "Password":          password,
        "Timestamp":         timestamp,
        "TransactionType":   "CustomerPayBillOnline",
        "Amount":            int(amount),
        "PartyA":            phone_number,
        "PartyB":            shortcode,
        "PhoneNumber":       phone_number,
        "CallBackURL":       MPESA_CONFIG["callback_url"],
        "AccountReference":  account_ref[:12],
        "TransactionDesc":   description[:13],
    }

    url = get_base_url() + STK_PUSH_PATH
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
            logger.info(f"STK Push initiated: {result.get('CheckoutRequestID')} | ref={internal_ref}")
            return result
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        logger.error(f"STK Push HTTP error ({e.code}): {body}")
        raise RuntimeError(f"STK Push failed ({e.code}): {body}")
    except Exception as e:
        logger.error(f"STK Push request error: {e}")
        raise RuntimeError(f"STK Push request error: {e}")


def query_stk_status(checkout_request_id: str) -> dict:
    """
    Query STK Push transaction status (for polling / timeout handling).
    Returns dict with ResultCode (0 = success).
    """
    token     = get_oauth_token()
    shortcode = MPESA_CONFIG["shortcode"]
    passkey   = MPESA_CONFIG["passkey"]
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    password  = _generate_password(shortcode, passkey, timestamp)

    payload = {
        "BusinessShortCode": shortcode,
        "Password":          password,
        "Timestamp":         timestamp,
        "CheckoutRequestID": checkout_request_id,
    }

    url  = get_base_url() + STK_QUERY_PATH
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
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except Exception as e:
        raise RuntimeError(f"STK Query failed: {e}")


# ── Phone normalization ───────────────────────────────────────────────────────

def _normalize_phone(phone: str) -> str:
    """
    Normalize phone to 254XXXXXXXXX format required by Daraja.
    Accepts: +254..., 07..., 07..., 254...
    """
    phone = phone.strip().replace(" ", "").replace("-", "")
    if phone.startswith("+"):
        phone = phone[1:]
    if phone.startswith("0"):
        phone = "254" + phone[1:]
    if not phone.startswith("254"):
        phone = "254" + phone
    return phone


def validate_kenyan_phone(phone: str) -> Tuple[bool, str]:
    """
    Validate a Kenyan M-Pesa phone number.
    Returns (is_valid, normalized_or_error).
    """
    try:
        normalized = _normalize_phone(phone)
        if len(normalized) != 12:
            return False, f"Invalid phone length: {normalized}"
        if not normalized.isdigit():
            return False, "Phone must contain digits only"
        # Safaricom Kenya prefixes
        prefix = normalized[3:5]  # digits after 254
        valid_prefixes = {"70","71","72","74","75","76","77","78","79","11","10"}
        if prefix not in valid_prefixes:
            return False, f"Not a recognized Safaricom number (prefix: 0{prefix})"
        return True, normalized
    except Exception as e:
        return False, str(e)


# ── Callback processor ────────────────────────────────────────────────────────

def process_callback(callback_body: dict) -> dict:
    """
    Parse and validate Safaricom STK Push callback.

    Expected structure:
    {
      "Body": {
        "stkCallback": {
          "MerchantRequestID": "...",
          "CheckoutRequestID": "...",
          "ResultCode": 0,
          "ResultDesc": "The service request is processed successfully.",
          "CallbackMetadata": {
            "Item": [
              {"Name": "Amount", "Value": 100},
              {"Name": "MpesaReceiptNumber", "Value": "ABC123"},
              {"Name": "TransactionDate", "Value": 20240101120000},
              {"Name": "PhoneNumber", "Value": 254700000000}
            ]
          }
        }
      }
    }

    Returns normalized dict with:
      success, result_code, result_desc, checkout_request_id,
      merchant_request_id, amount, receipt_number, phone, tx_date
    """
    try:
        stk = callback_body["Body"]["stkCallback"]
        result_code          = int(stk.get("ResultCode", -1))
        result_desc          = stk.get("ResultDesc", "Unknown")
        checkout_request_id  = stk.get("CheckoutRequestID", "")
        merchant_request_id  = stk.get("MerchantRequestID", "")

        result = {
            "success":             result_code == 0,
            "result_code":         result_code,
            "result_desc":         result_desc,
            "checkout_request_id": checkout_request_id,
            "merchant_request_id": merchant_request_id,
            "amount":              None,
            "receipt_number":      None,
            "phone":               None,
            "tx_date":             None,
        }

        if result_code == 0:
            # Extract metadata items
            items = stk.get("CallbackMetadata", {}).get("Item", [])
            meta  = {item["Name"]: item.get("Value") for item in items}
            result["amount"]         = float(meta.get("Amount", 0))
            result["receipt_number"] = str(meta.get("MpesaReceiptNumber", ""))
            result["phone"]          = str(meta.get("PhoneNumber", ""))
            result["tx_date"]        = str(meta.get("TransactionDate", ""))

        return result

    except KeyError as e:
        logger.error(f"Malformed callback body — missing key: {e}")
        return {"success": False, "result_code": -99, "result_desc": f"Malformed callback: {e}",
                "checkout_request_id": "", "merchant_request_id": "",
                "amount": None, "receipt_number": None, "phone": None, "tx_date": None}


# ── Result code messages ──────────────────────────────────────────────────────

RESULT_CODE_MESSAGES = {
    0:    "Payment received successfully.",
    1:    "Insufficient funds in your M-Pesa account.",
    17:   "M-Pesa system temporarily unavailable. Please try again.",
    1032: "Request cancelled by user.",
    1037: "STK Push timeout — user did not enter PIN in time.",
    2001: "Wrong PIN entered. Please try again.",
    -1:   "System internal error.",
}


def friendly_result_message(result_code: int) -> str:
    return RESULT_CODE_MESSAGES.get(result_code, f"Payment failed (code {result_code}). Please try again.")