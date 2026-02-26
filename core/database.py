"""
ChainPay Database Layer — v4.0 (Multi-Currency + First-Login Fix)
=================================================================
CHANGES FROM v3:
  1. users table: added `first_login_completed` (INTEGER DEFAULT 0).
  2. wallets table: added `locked_balance` column.
  3. create_user: creates wallet rows for ALL supported currencies at
     registration time (balance=0, locked_balance=0).
  4. ensure_all_currency_wallets(): migration helper — back-fills
     missing currency wallets for existing users.
  5. get_first_login_completed() / set_first_login_completed() helpers.
  6. update_balance: respects locked_balance when preventing overdraft.
  7. All previously existing functions preserved exactly.

SECURITY:
- Prepared statements throughout (SQL injection prevention)
- Foreign key enforcement (PRAGMA foreign_keys=ON)
- WAL journal mode for concurrent read safety
- Append-only audit_log (no DELETE permitted by convention)
- All amounts stored as INTEGER minor units (avoids float precision bugs)
"""

import sqlite3
import json
import time
import uuid
import os
from typing import Optional, List, Dict, Tuple
from contextlib import contextmanager
import hashlib
import hmac
import secrets


DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "chainpay.db")

# All currencies the system recognises.  Add new ones here ONLY.
SUPPORTED_CURRENCIES = ["USD", "EUR", "KES", "NGN", "GBP"]

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS roles (
    role_id     TEXT PRIMARY KEY,
    role_name   TEXT UNIQUE NOT NULL,
    permissions TEXT DEFAULT '{}',
    created_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    user_id                TEXT PRIMARY KEY,
    phone                  TEXT UNIQUE NOT NULL,
    name                   TEXT NOT NULL,
    pin_hash               TEXT NOT NULL,
    public_key             TEXT NOT NULL,
    private_key            TEXT NOT NULL,
    role_id                TEXT NOT NULL DEFAULT 'user',
    kyc_status             TEXT DEFAULT 'PENDING',
    is_active              INTEGER DEFAULT 1,
    is_suspended           INTEGER DEFAULT 0,
    created_at             REAL NOT NULL,
    last_login             REAL,
    base_country           TEXT DEFAULT 'Unknown',
    base_currency          TEXT DEFAULT 'USD',
    first_login_completed  INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS wallets (
    wallet_id      TEXT PRIMARY KEY,
    user_id        TEXT NOT NULL,
    currency       TEXT NOT NULL,
    balance        INTEGER DEFAULT 0,
    locked_balance INTEGER DEFAULT 0,
    created_at     REAL NOT NULL,
    updated_at     REAL,
    UNIQUE (user_id, currency)
);

CREATE TABLE IF NOT EXISTS transactions (
    tx_id       TEXT PRIMARY KEY,
    sender      TEXT NOT NULL,
    recipient   TEXT NOT NULL,
    amount      INTEGER NOT NULL,
    currency    TEXT NOT NULL,
    tx_type     TEXT NOT NULL,
    fee         INTEGER DEFAULT 0,
    timestamp   REAL NOT NULL,
    status      TEXT DEFAULT 'CONFIRMED',
    metadata    TEXT DEFAULT '{}',
    block_index INTEGER,
    signature   TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS fx_rates (
    pair       TEXT PRIMARY KEY,
    rate       REAL NOT NULL,
    spread_pct REAL DEFAULT 1.5,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS kyc_records (
    kyc_id      TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    id_type     TEXT NOT NULL,
    id_number   TEXT NOT NULL,
    verified_at REAL,
    status      TEXT DEFAULT 'PENDING',
    risk_score  INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS audit_log (
    log_id    TEXT PRIMARY KEY,
    user_id   TEXT,
    action    TEXT NOT NULL,
    details   TEXT DEFAULT '{}',
    ip_hash   TEXT DEFAULT '',
    timestamp REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS login_attempts (
    attempt_id TEXT PRIMARY KEY,
    phone      TEXT NOT NULL,
    success    INTEGER NOT NULL,
    ip_hash    TEXT DEFAULT '',
    user_agent TEXT DEFAULT '',
    timestamp  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS suspicious_activity (
    flag_id    TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL,
    flag_type  TEXT NOT NULL,
    severity   TEXT DEFAULT 'MEDIUM',
    details    TEXT DEFAULT '{}',
    resolved   INTEGER DEFAULT 0,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS system_config (
    config_key   TEXT PRIMARY KEY,
    config_value TEXT NOT NULL,
    updated_at   REAL NOT NULL,
    updated_by   TEXT DEFAULT 'SYSTEM'
);

CREATE INDEX IF NOT EXISTS idx_tx_sender      ON transactions(sender);
CREATE INDEX IF NOT EXISTS idx_tx_recipient   ON transactions(recipient);
CREATE INDEX IF NOT EXISTS idx_tx_timestamp   ON transactions(timestamp);
CREATE INDEX IF NOT EXISTS idx_tx_type        ON transactions(tx_type);
CREATE INDEX IF NOT EXISTS idx_wallet_user    ON wallets(user_id);
CREATE INDEX IF NOT EXISTS idx_login_phone    ON login_attempts(phone);
CREATE INDEX IF NOT EXISTS idx_login_ts       ON login_attempts(timestamp);
CREATE INDEX IF NOT EXISTS idx_suspicious_uid ON suspicious_activity(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_uid      ON audit_log(user_id);
CREATE INDEX IF NOT EXISTS idx_users_phone    ON users(phone);
CREATE INDEX IF NOT EXISTS idx_users_role     ON users(role_id);


CREATE TABLE IF NOT EXISTS otp_verifications (
    otp_id        TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    otp_hash      TEXT NOT NULL,
    expires_at    REAL NOT NULL,
    attempts      INTEGER DEFAULT 0,
    used          INTEGER DEFAULT 0,
    created_at    REAL NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

CREATE INDEX IF NOT EXISTS idx_otp_user    ON otp_verifications(user_id);
CREATE INDEX IF NOT EXISTS idx_otp_expires ON otp_verifications(expires_at);
"""

# Migration statements run safely with ALTER TABLE … ADD COLUMN IF NOT EXISTS
# (SQLite does not support IF NOT EXISTS on ALTER, so we try/ignore):
_MIGRATIONS = [
    # Add first_login_completed to pre-existing databases
    "ALTER TABLE users ADD COLUMN first_login_completed INTEGER DEFAULT 0",
    "ALTER TABLE users ADD COLUMN base_country TEXT DEFAULT 'Unknown'",
    "ALTER TABLE users ADD COLUMN base_currency TEXT DEFAULT 'USD'",
    # Add locked_balance to pre-existing wallets
    "ALTER TABLE wallets ADD COLUMN locked_balance INTEGER DEFAULT 0",
    "ALTER TABLE wallets ADD COLUMN updated_at REAL",
]


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA cache_size=10000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _direct_conn() -> sqlite3.Connection:
    """Direct connection for writes that happen INSIDE an already-open get_db() context."""
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    with get_db() as conn:
        conn.executescript(SCHEMA)
    _run_migrations()
    _seed_roles()
    _seed_fx_rates()
    _seed_system_config()


def _run_migrations():
    """Apply ALTER TABLE migrations safely — ignore 'duplicate column' errors."""
    conn = _direct_conn()
    try:
        for stmt in _MIGRATIONS:
            try:
                conn.execute(stmt)
                conn.commit()
            except sqlite3.OperationalError:
                # Column already exists — safe to ignore
                pass
    finally:
        conn.close()


def _seed_roles():
    roles = [
        ("user",       '["send_money","deposit","withdraw","fx_convert","view_own"]'),
        ("admin",      '["send_money","deposit","withdraw","fx_convert","view_all","manage_users","view_logs","system_config"]'),
        ("compliance", '["view_all","view_logs","flag_suspicious"]'),
    ]
    with get_db() as conn:
        for role_name, permissions in roles:
            conn.execute(
                "INSERT OR IGNORE INTO roles (role_id, role_name, permissions, created_at) VALUES (?,?,?,?)",
                (str(uuid.uuid4()), role_name, permissions, time.time())
            )


def _seed_fx_rates():
    rates = {
        "USD_EUR": 0.92,  "EUR_USD": 1.087,
        "USD_KES": 129.5, "KES_USD": 0.00772,
        "USD_NGN": 1580.0,"NGN_USD": 0.000633,
        "USD_GBP": 0.79,  "GBP_USD": 1.266,
        "EUR_KES": 140.8, "KES_EUR": 0.0071,
        "EUR_NGN": 1718.0,"NGN_EUR": 0.000582,
        "EUR_GBP": 0.859, "GBP_EUR": 1.164,
        "GBP_KES": 163.9, "KES_GBP": 0.0061,
        "GBP_NGN": 2002.0,"NGN_GBP": 0.000499,
        "KES_NGN": 12.2,  "NGN_KES": 0.082,
    }
    with get_db() as conn:
        for pair, rate in rates.items():
            conn.execute(
                "INSERT OR IGNORE INTO fx_rates (pair, rate, spread_pct, updated_at) VALUES (?,?,1.5,?)",
                (pair, rate, time.time())
            )


def _seed_system_config():
    defaults = {
        "daily_tx_limit_usd":  "5000",
        "single_tx_limit_usd": "2000",
        "max_deposit_usd":     "10000",
        "max_failed_login":    "5",
        "lockout_seconds":     "300",
        "maintenance_mode":    "false",
        "app_version":         "1.0.0",
    }
    with get_db() as conn:
        for key, value in defaults.items():
            conn.execute(
                "INSERT OR IGNORE INTO system_config (config_key, config_value, updated_at) VALUES (?,?,?)",
                (key, value, time.time())
            )


# ─── Phone / Country Helpers ──────────────────────────────────────────────────

def validate_phone_e164(phone: str) -> bool:
    import re
    pattern = r'^\+[1-9]\d{1,14}$'
    return bool(re.match(pattern, phone))


def detect_country_from_phone(phone: str) -> tuple:
    """Return (country_name, base_currency) from E.164 phone prefix."""
    if phone.startswith('+254'):
        return 'Kenya', 'KES'
    elif phone.startswith('+1'):
        return 'United States', 'USD'
    elif phone.startswith('+44'):
        return 'United Kingdom', 'GBP'
    elif phone.startswith('+233'):
        return 'Ghana', 'USD'   # GHS not in supported list; fall back to USD
    elif phone.startswith('+234'):
        return 'Nigeria', 'NGN'
    else:
        return 'Unknown', 'USD'


# ─── User Operations ──────────────────────────────────────────────────────────

def create_user(phone: str, name: str, pin_hash: str,
                public_key: str, private_key: str, role: str = "user") -> str:
    """
    Create a new user and initialise wallet rows for ALL supported currencies.
    first_login_completed is set to 0 (False) so the deposit popup fires once.
    """
    if not validate_phone_e164(phone):
        raise ValueError("Phone number must be in E.164 format (e.g., +254700000000)")

    user_id = str(uuid.uuid4())
    country, base_currency = detect_country_from_phone(phone)
    now = time.time()

    with get_db() as conn:
        conn.execute(
            """INSERT INTO users
               (user_id, phone, name, pin_hash, public_key, private_key,
                role_id, kyc_status, created_at, base_country, base_currency,
                first_login_completed)
               VALUES (?,?,?,?,?,?,?,'VERIFIED',?,?,?,0)""",
            (user_id, phone, name, pin_hash, public_key, private_key,
             role, now, country, base_currency)
        )

        # ── Create wallet rows for EVERY supported currency ────────────────
        for ccy in SUPPORTED_CURRENCIES:
            conn.execute(
                """INSERT OR IGNORE INTO wallets
                   (wallet_id, user_id, currency, balance, locked_balance, created_at, updated_at)
                   VALUES (?,?,?,0,0,?,?)""",
                (str(uuid.uuid4()), user_id, ccy, now, now)
            )

        # Inline audit (avoids nested get_db deadlock)
        conn.execute(
            """INSERT INTO audit_log
               (log_id, user_id, action, details, timestamp)
               VALUES (?,?,'ACCOUNT_CREATED',?,?)""",
            (str(uuid.uuid4()), user_id, json.dumps({
                "phone": phone,
                "name": name,
                "role": role,
                "base_country": country,
                "base_currency": base_currency,
                "wallets_created": SUPPORTED_CURRENCIES,
            }), now)
        )

    return user_id


def ensure_all_currency_wallets(user_id: str):
    """
    Back-fill wallet rows for any currencies missing for an existing user.
    Safe to call repeatedly — uses INSERT OR IGNORE.
    """
    now = time.time()
    with get_db() as conn:
        for ccy in SUPPORTED_CURRENCIES:
            conn.execute(
                """INSERT OR IGNORE INTO wallets
                   (wallet_id, user_id, currency, balance, locked_balance, created_at, updated_at)
                   VALUES (?,?,?,0,0,?,?)""",
                (str(uuid.uuid4()), user_id, ccy, now, now)
            )


def get_user_by_phone(phone: str) -> Optional[dict]:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE phone = ?", (phone,)).fetchone()
        return dict(row) if row else None


def get_user_by_phone_with_country(phone: str) -> Optional[dict]:
    """Get user by phone — country/currency already stored in DB row."""
    with get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE phone = ?", (phone,)).fetchone()
        return dict(row) if row else None


def get_user_by_id(user_id: str) -> Optional[dict]:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return dict(row) if row else None


def get_all_users(limit: int = 200) -> List[dict]:
    with get_db() as conn:
        rows = conn.execute(
            """SELECT user_id, phone, name, role_id, kyc_status,
                      is_active, is_suspended, created_at, last_login
               FROM users ORDER BY created_at DESC LIMIT ?""", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def update_last_login(user_id: str):
    with get_db() as conn:
        conn.execute("UPDATE users SET last_login = ? WHERE user_id = ?", (time.time(), user_id))


def get_first_login_completed(user_id: str) -> bool:
    """Return True if the user has already seen the first-login deposit popup."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT first_login_completed FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        if row is None:
            return True   # Unknown user — don't show popup
        return bool(row["first_login_completed"])


def set_first_login_completed(user_id: str):
    """Mark the first-login popup as shown so it never appears again."""
    with get_db() as conn:
        conn.execute(
            "UPDATE users SET first_login_completed = 1 WHERE user_id = ?", (user_id,)
        )


def set_user_role(user_id: str, role: str, admin_id: str) -> bool:
    if role not in {"user", "admin", "compliance"}:
        return False
    with get_db() as conn:
        conn.execute("UPDATE users SET role_id = ? WHERE user_id = ?", (role, user_id))
        conn.execute(
            "INSERT INTO audit_log (log_id, user_id, action, details, timestamp) VALUES (?,?,'ROLE_CHANGED',?,?)",
            (str(uuid.uuid4()), admin_id, json.dumps({"target": user_id, "new_role": role}), time.time())
        )
    return True


def suspend_user(user_id: str, admin_id: str, reason: str = "") -> bool:
    with get_db() as conn:
        conn.execute("UPDATE users SET is_suspended = 1 WHERE user_id = ?", (user_id,))
        conn.execute(
            "INSERT INTO audit_log (log_id, user_id, action, details, timestamp) VALUES (?,?,'USER_SUSPENDED',?,?)",
            (str(uuid.uuid4()), admin_id, json.dumps({"target": user_id, "reason": reason}), time.time())
        )
    return True


def unsuspend_user(user_id: str, admin_id: str) -> bool:
    with get_db() as conn:
        conn.execute("UPDATE users SET is_suspended = 0 WHERE user_id = ?", (user_id,))
        conn.execute(
            "INSERT INTO audit_log (log_id, user_id, action, details, timestamp) VALUES (?,?,'USER_UNSUSPENDED',?,?)",
            (str(uuid.uuid4()), admin_id, json.dumps({"target": user_id}), time.time())
        )
    return True


def update_pin(user_id: str, new_pin_hash: str):
    with get_db() as conn:
        conn.execute("UPDATE users SET pin_hash = ? WHERE user_id = ?", (new_pin_hash, user_id))


# ─── Wallet Operations ────────────────────────────────────────────────────────

def get_wallet(user_id: str, currency: str) -> Optional[dict]:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM wallets WHERE user_id = ? AND currency = ?", (user_id, currency)
        ).fetchone()
        return dict(row) if row else None


def get_all_wallets(user_id: str) -> List[dict]:
    """
    Return wallet rows for the user.
    Guarantees ALL supported currencies are present — back-fills if missing.
    """
    ensure_all_currency_wallets(user_id)
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM wallets WHERE user_id = ? ORDER BY currency", (user_id,)
        ).fetchall()
        return [dict(r) for r in rows]


# Alias used in server.py _patch_db
get_user_wallets = get_all_wallets


def get_balance(user_id: str, currency: str) -> float:
    """Return spendable balance (total minus locked) as a float."""
    wallet = get_wallet(user_id, currency)
    if not wallet:
        return 0.0
    spendable = wallet["balance"] - wallet.get("locked_balance", 0)
    return max(spendable, 0) / 100.0


def update_balance(conn: sqlite3.Connection, user_id: str, currency: str,
                   delta_minor: int) -> bool:
    """
    Atomic balance update within an existing transaction.
    Prevents overdraft: new_balance must be >= locked_balance.
    """
    row = conn.execute(
        "SELECT balance, locked_balance FROM wallets WHERE user_id = ? AND currency = ?",
        (user_id, currency)
    ).fetchone()
    if not row:
        return False

    locked      = row["locked_balance"] or 0
    new_balance = row["balance"] + delta_minor

    # Do not allow balance to drop below locked amount
    if new_balance < locked:
        return False

    conn.execute(
        "UPDATE wallets SET balance = ?, updated_at = ? WHERE user_id = ? AND currency = ?",
        (new_balance, time.time(), user_id, currency)
    )
    return True


def get_total_system_balances() -> Dict[str, float]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT currency, SUM(balance) as total FROM wallets GROUP BY currency ORDER BY currency"
        ).fetchall()
        return {r["currency"]: r["total"] / 100.0 for r in rows}


# ─── FX Rate Operations ───────────────────────────────────────────────────────

def get_fx_rate(from_ccy: str, to_ccy: str) -> Optional[Tuple[float, float]]:
    """Return (rate, spread_pct) or None."""
    pair = f"{from_ccy}_{to_ccy}"
    with get_db() as conn:
        row = conn.execute(
            "SELECT rate, spread_pct FROM fx_rates WHERE pair = ?", (pair,)
        ).fetchone()
        return (row["rate"], row["spread_pct"]) if row else None


def get_all_fx_rates() -> List[dict]:
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM fx_rates ORDER BY pair").fetchall()
        return [dict(r) for r in rows]


# ─── Transaction Operations ───────────────────────────────────────────────────

def record_transaction(tx_id: str, sender: str, recipient: str,
                        amount: float, currency: str, tx_type: str,
                        fee: float = 0.0, metadata: dict = None,
                        signature: str = "") -> bool:
    with get_db() as conn:
        conn.execute(
            """INSERT INTO transactions
               (tx_id, sender, recipient, amount, currency, tx_type,
                fee, timestamp, status, metadata, signature)
               VALUES (?,?,?,?,?,?,?,?,'CONFIRMED',?,?)""",
            (tx_id, sender, recipient, int(amount * 100), currency, tx_type,
             int(fee * 100), time.time(), json.dumps(metadata or {}), signature)
        )
    return True


def get_user_transactions(user_id: str, limit: int = 50) -> List[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM transactions WHERE sender=? OR recipient=? ORDER BY timestamp DESC LIMIT ?",
            (user_id, user_id, limit)
        ).fetchall()
        return _hydrate_txs(rows)


def get_all_transactions(limit: int = 200) -> List[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM transactions ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
        return _hydrate_txs(rows)


def _hydrate_txs(rows) -> List[dict]:
    results = []
    for r in rows:
        d = dict(r)
        d["amount"] = d["amount"] / 100.0
        d["fee"]    = d["fee"] / 100.0
        try:
            d["metadata"] = json.loads(d.get("metadata") or "{}")
        except Exception:
            d["metadata"] = {}
        results.append(d)
    return results


def get_tx_stats() -> dict:
    with get_db() as conn:
        total   = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        failed  = conn.execute("SELECT COUNT(*) FROM transactions WHERE status != 'CONFIRMED'").fetchone()[0]
        revenue = conn.execute("SELECT SUM(fee) FROM transactions").fetchone()[0] or 0
        by_type = conn.execute(
            "SELECT tx_type, COUNT(*) as count, SUM(amount) as volume FROM transactions GROUP BY tx_type"
        ).fetchall()
        by_ccy  = conn.execute(
            "SELECT currency, COUNT(*) as count, SUM(amount) as volume FROM transactions GROUP BY currency"
        ).fetchall()
        return {
            "total_transactions":  total,
            "failed_transactions": failed,
            "total_revenue":       revenue / 100.0,
            "by_type":   [dict(r) for r in by_type],
            "by_currency": [dict(r) for r in by_ccy],
        }


# ─── Audit & Security ─────────────────────────────────────────────────────────

def audit_action(user_id: str, action: str, details: dict):
    """Write to audit log using a direct connection (safe inside get_db context)."""
    conn = _direct_conn()
    try:
        conn.execute(
            "INSERT INTO audit_log (log_id, user_id, action, details, timestamp) VALUES (?,?,?,?,?)",
            (str(uuid.uuid4()), user_id, action, json.dumps(details), time.time())
        )
        conn.commit()
    finally:
        conn.close()


def get_audit_log(limit: int = 200) -> List[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            try:
                d["details"] = json.loads(d.get("details") or "{}")
            except Exception:
                d["details"] = {}
            results.append(d)
        return results


def record_login_attempt(phone: str, success: bool, ip_hash: str = "",
                          user_agent: str = ""):
    with get_db() as conn:
        conn.execute(
            """INSERT INTO login_attempts
               (attempt_id, phone, success, ip_hash, user_agent, timestamp)
               VALUES (?,?,?,?,?,?)""",
            (str(uuid.uuid4()), phone, int(success), ip_hash, user_agent, time.time())
        )


def get_failed_login_count(phone: str, window_seconds: int = 300) -> int:
    cutoff = time.time() - window_seconds
    with get_db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM login_attempts WHERE phone=? AND success=0 AND timestamp>?",
            (phone, cutoff)
        ).fetchone()
        return row[0]


def get_login_attempts(limit: int = 200) -> List[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM login_attempts ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def flag_suspicious_activity(user_id: str, flag_type: str, severity: str,
                              details: dict = None):
    with get_db() as conn:
        conn.execute(
            """INSERT INTO suspicious_activity
               (flag_id, user_id, flag_type, severity, details, resolved, created_at)
               VALUES (?,?,?,?,?,0,?)""",
            (str(uuid.uuid4()), user_id, flag_type, severity,
             json.dumps(details or {}), time.time())
        )


def get_suspicious_activity(resolved: Optional[bool] = None) -> List[dict]:
    with get_db() as conn:
        if resolved is None:
            rows = conn.execute(
                "SELECT * FROM suspicious_activity ORDER BY created_at DESC"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM suspicious_activity WHERE resolved=? ORDER BY created_at DESC",
                (int(resolved),)
            ).fetchall()
        return [dict(r) for r in rows]


def resolve_suspicious_flag(flag_id: str):
    with get_db() as conn:
        conn.execute(
            "UPDATE suspicious_activity SET resolved=1 WHERE flag_id=?", (flag_id,)
        )


# ─── System Config ────────────────────────────────────────────────────────────

def get_config(key: str) -> Optional[str]:
    with get_db() as conn:
        row = conn.execute(
            "SELECT config_value FROM system_config WHERE config_key=?", (key,)
        ).fetchone()
        return row["config_value"] if row else None


def set_config(key: str, value: str, updated_by: str = "SYSTEM"):
    with get_db() as conn:
        conn.execute(
            """INSERT INTO system_config (config_key, config_value, updated_at, updated_by)
               VALUES (?,?,?,?)
               ON CONFLICT(config_key) DO UPDATE SET
                   config_value=excluded.config_value,
                   updated_at=excluded.updated_at,
                   updated_by=excluded.updated_by""",
            (key, value, time.time(), updated_by)
        )


def get_all_config() -> List[dict]:
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM system_config ORDER BY config_key").fetchall()
        return [dict(r) for r in rows]


# ─── Admin Stats ──────────────────────────────────────────────────────────────

def get_system_stats() -> dict:
    with get_db() as conn:
        total_users     = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        active_users    = conn.execute("SELECT COUNT(*) FROM users WHERE is_active=1 AND is_suspended=0").fetchone()[0]
        suspended_users = conn.execute("SELECT COUNT(*) FROM users WHERE is_suspended=1").fetchone()[0]
        total_txs       = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        failed_txs      = conn.execute("SELECT COUNT(*) FROM transactions WHERE status!='CONFIRMED'").fetchone()[0]
        revenue_raw     = conn.execute("SELECT SUM(fee) FROM transactions").fetchone()[0] or 0
        sus_flags       = conn.execute("SELECT COUNT(*) FROM suspicious_activity WHERE resolved=0").fetchone()[0]
        now             = time.time()
        failed_24h      = conn.execute(
            "SELECT COUNT(*) FROM login_attempts WHERE success=0 AND timestamp>?",
            (now - 86400,)
        ).fetchone()[0]
        total_logins_24h = conn.execute(
            "SELECT COUNT(*) FROM login_attempts WHERE timestamp>?", (now - 86400,)
        ).fetchone()[0]
        db_size = os.path.getsize(DB_PATH) / 1024 if os.path.exists(DB_PATH) else 0.0

        return {
            "total_users":       total_users,
            "active_users":      active_users,
            "suspended_users":   suspended_users,
            "total_transactions": total_txs,
            "failed_transactions": failed_txs,
            "total_volume_usd":  0.0,   # Would need FX pivot; skip for stats
            "total_revenue":     revenue_raw / 100.0,
            "suspicious_flags":  sus_flags,
            "failed_logins_24h": failed_24h,
            "total_logins_24h":  total_logins_24h,
            "db_size_kb":        round(db_size, 2),
        }


# ─── Session helper (used by security.py) ────────────────────────────────────

def clear_failed_attempts(phone: str):
    """Remove failed login attempts for phone (after successful login)."""
    with get_db() as conn:
        conn.execute(
            "DELETE FROM login_attempts WHERE phone=? AND success=0", (phone,)
        )


# ─── OTP Verification ────────────────────────────────────────────────────────

import secrets
import hmac

OTP_EXPIRY_SECONDS = 300       # 5 minutes
OTP_MAX_ATTEMPTS   = 3

def _hash_otp(otp: str) -> str:
    """SHA-256 hash of OTP. Never store plaintext."""
    return hashlib.sha256(otp.encode()).hexdigest()

def generate_otp() -> str:
    """Cryptographically secure 6-digit OTP."""
    return f"{secrets.randbelow(1000000):06d}"

def create_otp(user_id: str) -> str:
    """
    Invalidate any existing unused OTPs for this user,
    create a new hashed OTP record, return the plaintext OTP
    (caller sends it via SMS and never stores it).
    """
    otp       = generate_otp()
    otp_hash  = _hash_otp(otp)
    otp_id    = str(uuid.uuid4())
    now       = time.time()
    expires   = now + OTP_EXPIRY_SECONDS

    with get_db() as conn:
        # Invalidate all previous unused OTPs for this user (prevent reuse/replay)
        conn.execute(
            "UPDATE otp_verifications SET used=1 WHERE user_id=? AND used=0",
            (user_id,)
        )
        conn.execute(
            """INSERT INTO otp_verifications
               (otp_id, user_id, otp_hash, expires_at, attempts, used, created_at)
               VALUES (?,?,?,?,0,0,?)""",
            (otp_id, user_id, otp_hash, expires, now)
        )
    return otp


def verify_otp(user_id: str, otp_input: str) -> dict:
    """
    Verify an OTP for a given user.
    Returns {"ok": True} or {"ok": False, "reason": str}.
    
    Security:
    - Timing-safe comparison
    - Checks expiry, used status, and attempt limit
    - Marks as used on success
    - Increments attempt counter on failure
    - Locks out after OTP_MAX_ATTEMPTS failures
    """
    now = time.time()

    with get_db() as conn:
        row = conn.execute(
            """SELECT otp_id, otp_hash, expires_at, attempts, used
               FROM otp_verifications
               WHERE user_id=? AND used=0
               ORDER BY created_at DESC LIMIT 1""",
            (user_id,)
        ).fetchone()

        if not row:
            return {"ok": False, "reason": "No pending OTP found. Please request a new code."}

        otp_id    = row["otp_id"]
        otp_hash  = row["otp_hash"]
        expires   = row["expires_at"]
        attempts  = row["attempts"]
        used      = row["used"]

        if used:
            return {"ok": False, "reason": "OTP already used."}

        if now > expires:
            conn.execute("UPDATE otp_verifications SET used=1 WHERE otp_id=?", (otp_id,))
            return {"ok": False, "reason": "OTP has expired. Please request a new code."}

        if attempts >= OTP_MAX_ATTEMPTS:
            conn.execute("UPDATE otp_verifications SET used=1 WHERE otp_id=?", (otp_id,))
            return {"ok": False, "reason": "Too many incorrect attempts. Please request a new code."}

        # Timing-safe hash comparison
        input_hash = _hash_otp(otp_input)
        match = hmac.compare_digest(otp_hash, input_hash)

        if not match:
            conn.execute(
                "UPDATE otp_verifications SET attempts=attempts+1 WHERE otp_id=?",
                (otp_id,)
            )
            remaining = OTP_MAX_ATTEMPTS - (attempts + 1)
            if remaining <= 0:
                conn.execute("UPDATE otp_verifications SET used=1 WHERE otp_id=?", (otp_id,))
                return {"ok": False, "reason": "Too many incorrect attempts. Please request a new code."}
            return {"ok": False, "reason": f"Incorrect OTP. {remaining} attempt(s) remaining."}

        # Valid — mark used
        conn.execute(
            "UPDATE otp_verifications SET used=1, attempts=attempts+1 WHERE otp_id=?",
            (otp_id,)
        )
        return {"ok": True}


def cleanup_expired_otps():
    """Delete OTP records older than 1 hour. Call from background task."""
    with get_db() as conn:
        conn.execute(
            "DELETE FROM otp_verifications WHERE expires_at < ?",
            (time.time() - 3600,)
        )

# ─── Notifications ────────────────────────────────────────────────────────────

def notify_user(user_id: str, notification_type: str, message: str,
                data: dict = None):
    """Create a user notification. Silently ignores missing notifications table."""
    try:
        with get_db() as conn:
            # Ensure table exists
            conn.execute("""
                CREATE TABLE IF NOT EXISTS notifications (
                    notification_id TEXT PRIMARY KEY,
                    user_id         TEXT NOT NULL,
                    type            TEXT NOT NULL,
                    message         TEXT NOT NULL,
                    data            TEXT DEFAULT '{}',
                    created_at      REAL NOT NULL,
                    read            INTEGER DEFAULT 0
                )
            """)
            conn.execute(
                """INSERT INTO notifications
                   (notification_id, user_id, type, message, data, created_at, read)
                   VALUES (?,?,?,?,?,?,0)""",
                (str(uuid.uuid4()), user_id, notification_type, message,
                 json.dumps(data or {}), time.time())
            )
    except Exception:
        pass   # Notifications are non-critical


def get_user_notifications(user_id: str, unread_only: bool = False) -> List[dict]:
    try:
        with get_db() as conn:
            query = "SELECT * FROM notifications WHERE user_id = ?"
            if unread_only:
                query += " AND read = 0"
            query += " ORDER BY created_at DESC LIMIT 50"
            rows = conn.execute(query, (user_id,)).fetchall()
            return [dict(r) for r in rows]
    except Exception:
        return []


def mark_notification_read(notification_id: str):
    try:
        with get_db() as conn:
            conn.execute(
                "UPDATE notifications SET read = 1 WHERE notification_id = ?",
                (notification_id,)
            )
    except Exception:
        pass


# ─── Reversal helpers (called by server.py) ───────────────────────────────────

def approve_reversal(reversal_id: str, admin_id: str, note: str = "") -> dict:
    """Approve reversal request and execute atomic balance reversal."""
    conn = _direct_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")

        rev = conn.execute(
            """SELECT rr.*, t.amount as tx_amount, t.currency, t.sender, t.recipient
               FROM reversal_requests rr
               JOIN transactions t ON rr.tx_id = t.tx_id
               WHERE rr.reversal_id = ? AND rr.status = 'PENDING'""",
            (reversal_id,)
        ).fetchone()

        if not rev:
            conn.rollback()
            return {"success": False, "message": "Reversal request not found"}

        tx_amount    = rev['tx_amount']
        currency     = rev['currency']
        sender_id    = rev['sender']
        recipient_id = rev['recipient']

        sender_bal = conn.execute(
            "SELECT balance FROM wallets WHERE user_id = ? AND currency = ?",
            (sender_id, currency)
        ).fetchone()
        recipient_bal = conn.execute(
            "SELECT balance FROM wallets WHERE user_id = ? AND currency = ?",
            (recipient_id, currency)
        ).fetchone()

        if not sender_bal or not recipient_bal:
            conn.rollback()
            return {"success": False, "message": "Wallet not found"}

        if recipient_bal['balance'] < tx_amount:
            conn.rollback()
            return {"success": False, "message": "Recipient has insufficient funds for reversal"}

        conn.execute(
            "UPDATE wallets SET balance = balance + ?, updated_at = ? WHERE user_id = ? AND currency = ?",
            (tx_amount, time.time(), sender_id, currency)
        )
        conn.execute(
            "UPDATE wallets SET balance = balance - ?, updated_at = ? WHERE user_id = ? AND currency = ?",
            (tx_amount, time.time(), recipient_id, currency)
        )

        reversal_tx_id = str(uuid.uuid4())
        conn.execute(
            """INSERT INTO transactions
               (tx_id, sender, recipient, amount, currency, tx_type, fee, timestamp, status, metadata)
               VALUES (?,?,?,?,?,'REVERSAL',0,?,'CONFIRMED',?)""",
            (reversal_tx_id, recipient_id, sender_id, tx_amount, currency, time.time(),
             json.dumps({"original_tx_id": rev['tx_id'],
                         "reversal_id": reversal_id, "admin_id": admin_id}))
        )

        conn.execute(
            "UPDATE transactions SET status = 'REVERSED' WHERE tx_id = ?", (rev['tx_id'],)
        )
        conn.execute(
            """UPDATE reversal_requests SET
               status='APPROVED', admin_id=?, admin_note=?, reviewed_at=?
               WHERE reversal_id=?""",
            (admin_id, note, time.time(), reversal_id)
        )
        conn.execute(
            """INSERT INTO audit_log (log_id, user_id, action, details, timestamp)
               VALUES (?,?,'REVERSAL_APPROVED',?,?)""",
            (str(uuid.uuid4()), admin_id, json.dumps({
                "reversal_id": reversal_id, "tx_id": rev['tx_id'],
                "amount": tx_amount / 100.0, "currency": currency
            }), time.time())
        )

        conn.commit()
        return {"success": True, "message": "Reversal approved and executed",
                "reversal_tx_id": reversal_tx_id}
    except Exception as e:
        conn.rollback()
        return {"success": False, "message": str(e)}
    finally:
        conn.close()


def reject_reversal(reversal_id: str, admin_id: str, note: str = "") -> dict:
    conn = _direct_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        rev = conn.execute(
            "SELECT * FROM reversal_requests WHERE reversal_id = ? AND status = 'PENDING'",
            (reversal_id,)
        ).fetchone()
        if not rev:
            conn.rollback()
            return {"success": False, "message": "Reversal request not found"}

        conn.execute(
            """UPDATE reversal_requests SET
               status='REJECTED', admin_id=?, admin_note=?, reviewed_at=?
               WHERE reversal_id=?""",
            (admin_id, note, time.time(), reversal_id)
        )
        conn.execute(
            "UPDATE transactions SET status='CONFIRMED' WHERE tx_id=?", (rev['tx_id'],)
        )
        conn.execute(
            """INSERT INTO audit_log (log_id, user_id, action, details, timestamp)
               VALUES (?,?,'REVERSAL_REJECTED',?,?)""",
            (str(uuid.uuid4()), admin_id, json.dumps({
                "reversal_id": reversal_id, "tx_id": rev['tx_id'], "reason": note
            }), time.time())
        )
        conn.commit()
        return {"success": True, "message": "Reversal rejected"}
    except Exception as e:
        conn.rollback()
        return {"success": False, "message": str(e)}
    finally:
        conn.close()


def get_reversal_history(filters: dict = None) -> List[dict]:
    query = """
        SELECT rr.*,
               t.amount, t.currency, t.sender, t.recipient, t.timestamp as tx_timestamp,
               u.name  as requester_name, u.phone as requester_phone,
               r2.name as recipient_name, r2.phone as recipient_phone
        FROM reversal_requests rr
        JOIN transactions t  ON rr.tx_id        = t.tx_id
        JOIN users         u  ON rr.requester_id = u.user_id
        JOIN users         r2 ON t.recipient     = r2.user_id
        WHERE 1=1
    """
    params = []
    if filters:
        if filters.get('status'):
            query += " AND rr.status = ?"
            params.append(filters['status'])
        if filters.get('from_date'):
            query += " AND rr.created_at >= ?"
            params.append(filters['from_date'])
        if filters.get('to_date'):
            query += " AND rr.created_at <= ?"
            params.append(filters['to_date'])
    query += " ORDER BY rr.created_at DESC LIMIT 200"

    with get_db() as conn:
        rows = conn.execute(query, params).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            d['amount'] = d['amount'] / 100.0
            results.append(d)
        return results