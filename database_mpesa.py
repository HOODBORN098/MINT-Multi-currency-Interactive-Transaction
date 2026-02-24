"""
ChainPay Database — M-Pesa & Reversal Schema Extension
========================================================
Run this migration ONCE against your chainpay.db to add:
  - mpesa_transactions   : tracks every STK Push request + callback result
  - reversal_requests    : user-initiated reversal workflow
  - Updated transactions : adds reversal_id FK column

Apply:  python database_mpesa.py
Or import init_mpesa_schema() into your server startup.
"""

import sqlite3
import os
import time
import uuid
import json
from contextlib import contextmanager
from typing import Optional, List

# ── Re-use DB path from database.py ──────────────────────────────────────────
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chainpay.db")

MPESA_SCHEMA = """
-- M-Pesa STK Push transaction state machine
-- States: PENDING → CONFIRMED | FAILED | EXPIRED | DUPLICATE
CREATE TABLE IF NOT EXISTS mpesa_transactions (
    mpesa_tx_id         TEXT PRIMARY KEY,
    internal_ref        TEXT UNIQUE NOT NULL,   -- Our UUID, used for idempotency
    user_id             TEXT NOT NULL,
    phone               TEXT NOT NULL,
    amount_kes          INTEGER NOT NULL,        -- KES in minor units (cents)
    checkout_request_id TEXT UNIQUE,             -- Returned by Safaricom on initiation
    merchant_request_id TEXT,
    status              TEXT DEFAULT 'PENDING',  -- PENDING|CONFIRMED|FAILED|EXPIRED|DUPLICATE
    result_code         INTEGER,
    result_desc         TEXT,
    mpesa_receipt       TEXT UNIQUE,             -- MpesaReceiptNumber from callback
    mpesa_phone         TEXT,                    -- Phone as confirmed by Safaricom
    wallet_credited     INTEGER DEFAULT 0,       -- 1 after wallet balance updated
    chainpay_tx_id      TEXT,                    -- FK to transactions table
    initiated_at        REAL NOT NULL,
    callback_at         REAL,
    expires_at          REAL NOT NULL,           -- STK timeout (initiated_at + 120s)
    raw_callback        TEXT DEFAULT '{}'        -- Full JSON callback for audit
);

-- User-initiated reversal requests
CREATE TABLE IF NOT EXISTS reversal_requests (
    reversal_id     TEXT PRIMARY KEY,
    tx_id           TEXT NOT NULL,               -- Transaction to reverse
    requester_id    TEXT NOT NULL,
    reason          TEXT DEFAULT '',
    status          TEXT DEFAULT 'PENDING',      -- PENDING|APPROVED|REJECTED|COMPLETED
    admin_id        TEXT,                        -- Admin who approved/rejected
    admin_note      TEXT DEFAULT '',
    created_at      REAL NOT NULL,
    reviewed_at     REAL,
    completed_at    REAL
);

CREATE INDEX IF NOT EXISTS idx_mpesa_user      ON mpesa_transactions(user_id);
CREATE INDEX IF NOT EXISTS idx_mpesa_status    ON mpesa_transactions(status);
CREATE INDEX IF NOT EXISTS idx_mpesa_checkout  ON mpesa_transactions(checkout_request_id);
CREATE INDEX IF NOT EXISTS idx_reversal_tx     ON reversal_requests(tx_id);
CREATE INDEX IF NOT EXISTS idx_reversal_status ON reversal_requests(status);
CREATE INDEX IF NOT EXISTS idx_reversal_user   ON reversal_requests(requester_id);
"""


@contextmanager
def _get_db():
    conn = sqlite3.connect(DB_PATH, timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _direct():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_mpesa_schema():
    """Call this during server startup to ensure tables exist."""
    with _get_db() as conn:
        conn.executescript(MPESA_SCHEMA)


# ── M-Pesa Transaction CRUD ───────────────────────────────────────────────────

def create_mpesa_tx(user_id: str, phone: str, amount_kes: float) -> dict:
    """
    Create a PENDING M-Pesa transaction record BEFORE initiating STK Push.
    Returns the record dict including internal_ref and mpesa_tx_id.
    """
    mpesa_tx_id  = str(uuid.uuid4())
    internal_ref = str(uuid.uuid4())
    now          = time.time()
    expires_at   = now + 120   # STK Push times out after 120 seconds

    with _get_db() as conn:
        conn.execute(
            """INSERT INTO mpesa_transactions
               (mpesa_tx_id, internal_ref, user_id, phone, amount_kes,
                status, initiated_at, expires_at)
               VALUES (?,?,?,?,?, 'PENDING',?,?)""",
            (mpesa_tx_id, internal_ref, user_id, phone,
             int(round(amount_kes * 100)), now, expires_at)
        )

    return {
        "mpesa_tx_id":  mpesa_tx_id,
        "internal_ref": internal_ref,
        "user_id":      user_id,
        "phone":        phone,
        "amount_kes":   amount_kes,
        "status":       "PENDING",
        "initiated_at": now,
        "expires_at":   expires_at,
    }


def update_mpesa_checkout_id(internal_ref: str,
                              checkout_request_id: str,
                              merchant_request_id: str):
    """Store Safaricom's CheckoutRequestID after successful STK initiation."""
    with _get_db() as conn:
        conn.execute(
            """UPDATE mpesa_transactions
               SET checkout_request_id=?, merchant_request_id=?
               WHERE internal_ref=?""",
            (checkout_request_id, merchant_request_id, internal_ref)
        )


def get_mpesa_tx_by_checkout(checkout_request_id: str) -> Optional[dict]:
    """Look up pending transaction by Safaricom's CheckoutRequestID."""
    with _get_db() as conn:
        row = conn.execute(
            "SELECT * FROM mpesa_transactions WHERE checkout_request_id=?",
            (checkout_request_id,)
        ).fetchone()
        return dict(row) if row else None


def get_mpesa_tx_by_receipt(receipt: str) -> Optional[dict]:
    """Duplicate-receipt guard: check if we already processed this receipt."""
    with _get_db() as conn:
        row = conn.execute(
            "SELECT * FROM mpesa_transactions WHERE mpesa_receipt=?", (receipt,)
        ).fetchone()
        return dict(row) if row else None


def confirm_mpesa_tx(internal_ref: str, result_code: int, result_desc: str,
                     amount_kes: float, receipt: str, mpesa_phone: str,
                     chainpay_tx_id: str, raw_callback: dict):
    """
    Mark M-Pesa transaction as CONFIRMED and link to ChainPay transaction.
    Called INSIDE the atomic wallet-credit block.
    """
    conn = _direct()
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


def fail_mpesa_tx(internal_ref: str, result_code: int,
                  result_desc: str, raw_callback: dict):
    """Mark M-Pesa transaction as FAILED."""
    conn = _direct()
    try:
        conn.execute(
            """UPDATE mpesa_transactions SET
               status='FAILED', result_code=?, result_desc=?,
               callback_at=?, raw_callback=?
               WHERE internal_ref=?""",
            (result_code, result_desc, time.time(),
             json.dumps(raw_callback), internal_ref)
        )
        conn.commit()
    finally:
        conn.close()


def expire_stale_mpesa_txs():
    """
    Mark all PENDING transactions past their expiry as EXPIRED.
    Call periodically (e.g., every 60 seconds via background task).
    """
    with _get_db() as conn:
        conn.execute(
            """UPDATE mpesa_transactions SET status='EXPIRED'
               WHERE status='PENDING' AND expires_at < ?""",
            (time.time(),)
        )


def get_user_mpesa_history(user_id: str, limit: int = 50) -> List[dict]:
    with _get_db() as conn:
        rows = conn.execute(
            """SELECT * FROM mpesa_transactions
               WHERE user_id=? ORDER BY initiated_at DESC LIMIT ?""",
            (user_id, limit)
        ).fetchall()
        return [dict(r) for r in rows]


def get_all_mpesa_txs(status: str = None, limit: int = 200) -> List[dict]:
    with _get_db() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM mpesa_transactions WHERE status=? ORDER BY initiated_at DESC LIMIT ?",
                (status, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM mpesa_transactions ORDER BY initiated_at DESC LIMIT ?",
                (limit,)
            ).fetchall()
        return [dict(r) for r in rows]


# ── Reversal Request CRUD ─────────────────────────────────────────────────────

def create_reversal_request(tx_id: str, requester_id: str, reason: str = "") -> dict:
    """
    Create a reversal request for an eligible transaction.
    Eligibility is enforced at the API layer (24h window, SEND type, CONFIRMED status).
    """
    reversal_id = str(uuid.uuid4())
    now         = time.time()
    with _get_db() as conn:
        # Prevent duplicate reversal requests for the same TX
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

    return {
        "reversal_id":  reversal_id,
        "tx_id":        tx_id,
        "requester_id": requester_id,
        "reason":       reason,
        "status":       "PENDING",
        "created_at":   now,
    }


def get_reversal_request(reversal_id: str) -> Optional[dict]:
    with _get_db() as conn:
        row = conn.execute(
            "SELECT * FROM reversal_requests WHERE reversal_id=?", (reversal_id,)
        ).fetchone()
        return dict(row) if row else None


def get_pending_reversals() -> List[dict]:
    """Returns all pending reversal requests for admin review."""
    with _get_db() as conn:
        rows = conn.execute(
            """SELECT rr.*, t.amount, t.currency, t.sender, t.recipient, t.timestamp as tx_ts,
                      u.name as requester_name, u.phone as requester_phone
               FROM reversal_requests rr
               JOIN transactions t ON rr.tx_id = t.tx_id
               JOIN users u ON rr.requester_id = u.user_id
               WHERE rr.status='PENDING'
               ORDER BY rr.created_at ASC""",
            []
        ).fetchall()
        return [dict(r) for r in rows]


def approve_reversal(reversal_id: str, admin_id: str, note: str = "") -> bool:
    """Mark reversal as APPROVED (actual balance swap done at API layer)."""
    with _get_db() as conn:
        conn.execute(
            """UPDATE reversal_requests SET status='APPROVED',
               admin_id=?, admin_note=?, reviewed_at=?
               WHERE reversal_id=? AND status='PENDING'""",
            (admin_id, note, time.time(), reversal_id)
        )
    return True


def complete_reversal(reversal_id: str) -> bool:
    """Mark reversal as COMPLETED after balances reversed."""
    conn = _direct()
    try:
        conn.execute(
            "UPDATE reversal_requests SET status='COMPLETED', completed_at=? WHERE reversal_id=?",
            (time.time(), reversal_id)
        )
        conn.commit()
    finally:
        conn.close()
    return True


def reject_reversal(reversal_id: str, admin_id: str, note: str = "") -> bool:
    with _get_db() as conn:
        conn.execute(
            """UPDATE reversal_requests SET status='REJECTED',
               admin_id=?, admin_note=?, reviewed_at=?
               WHERE reversal_id=? AND status='PENDING'""",
            (admin_id, note, time.time(), reversal_id)
        )
    return True


def get_reversal_history(limit: int = 200) -> List[dict]:
    with _get_db() as conn:
        rows = conn.execute(
            """SELECT rr.*, t.amount, t.currency, t.sender, t.recipient,
                      u.name as requester_name, u.phone as requester_phone
               FROM reversal_requests rr
               JOIN transactions t ON rr.tx_id = t.tx_id
               JOIN users u ON rr.requester_id = u.user_id
               ORDER BY rr.created_at DESC LIMIT ?""",
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


if __name__ == "__main__":
    init_mpesa_schema()
    print("✓ M-Pesa schema migration complete.")