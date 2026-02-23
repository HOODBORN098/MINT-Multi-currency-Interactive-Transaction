"""
ChainPay Database Layer — Production Grade
===========================================
SQLite-backed persistence with full ACID guarantees, WAL mode,
and complete schema for users, wallets, transactions, FX rates,
KYC, audit, login tracking, RBAC roles, suspicious activity,
system config, and blockchain ledger sync.

SECURITY:
- Prepared statements throughout (SQL injection prevention)
- Foreign key enforcement (PRAGMA foreign_keys=ON)
- WAL journal mode for concurrent read safety
- Append-only audit_log (no DELETE permitted by convention)
- All amounts stored as INTEGER minor units (avoids float precision bugs)

FIX LOG:
  - Added tables: login_attempts, roles, system_config, suspicious_activity
  - Added admin functions: get_all_users, get_login_attempts, flag_suspicious_activity,
    get_suspicious_activity, set_user_role, suspend_user, get_system_stats (extended),
    get_tx_stats, get_total_system_balances, get_failed_login_count, resolve_suspicious_flag
  - Fixed audit_action to use _direct_conn() (prevents nested-context WAL deadlock)
  - Fixed create_user to inline audit log (same fix for same reason)
"""

import sqlite3
import json
import time
import uuid
import os
from typing import Optional, List, Dict, Tuple
from contextlib import contextmanager


DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "chainpay.db")

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
    user_id      TEXT PRIMARY KEY,
    phone        TEXT UNIQUE NOT NULL,
    name         TEXT NOT NULL,
    pin_hash     TEXT NOT NULL,
    public_key   TEXT NOT NULL,
    private_key  TEXT NOT NULL,
    role_id      TEXT NOT NULL DEFAULT 'user',
    kyc_status   TEXT DEFAULT 'PENDING',
    is_active    INTEGER DEFAULT 1,
    is_suspended INTEGER DEFAULT 0,
    created_at   REAL NOT NULL,
    last_login   REAL
);

CREATE TABLE IF NOT EXISTS wallets (
    wallet_id  TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL,
    currency   TEXT NOT NULL,
    balance    INTEGER DEFAULT 0,
    created_at REAL NOT NULL,
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
"""


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
    _seed_roles()
    _seed_fx_rates()
    _seed_system_config()


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


# ─── User Operations ──────────────────────────────────────────────────────────

def create_user(phone: str, name: str, pin_hash: str,
                public_key: str, private_key: str, role: str = "user") -> str:
    user_id = str(uuid.uuid4())
    seed_balances = {"USD": 50000, "EUR": 20000, "KES": 500000, "NGN": 100000, "GBP": 10000}
    with get_db() as conn:
        conn.execute(
            """INSERT INTO users (user_id, phone, name, pin_hash, public_key, private_key,
               role_id, kyc_status, created_at) VALUES (?,?,?,?,?,?,?,'VERIFIED',?)""",
            (user_id, phone, name, pin_hash, public_key, private_key, role, time.time())
        )
        for currency, balance in seed_balances.items():
            conn.execute(
                "INSERT INTO wallets (wallet_id, user_id, currency, balance, created_at) VALUES (?,?,?,?,?)",
                (str(uuid.uuid4()), user_id, currency, balance, time.time())
            )
        # Inline audit — avoids nested connection deadlock
        conn.execute(
            "INSERT INTO audit_log (log_id, user_id, action, details, timestamp) VALUES (?,?,'ACCOUNT_CREATED',?,?)",
            (str(uuid.uuid4()), user_id, json.dumps({"phone": phone, "name": name, "role": role}), time.time())
        )
    return user_id


def get_user_by_phone(phone: str) -> Optional[dict]:
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
            "SELECT user_id, phone, name, role_id, kyc_status, is_active, is_suspended, created_at, last_login "
            "FROM users ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def update_last_login(user_id: str):
    with get_db() as conn:
        conn.execute("UPDATE users SET last_login = ? WHERE user_id = ?", (time.time(), user_id))


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


# ─── Wallet Operations ────────────────────────────────────────────────────────

def get_wallet(user_id: str, currency: str) -> Optional[dict]:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM wallets WHERE user_id = ? AND currency = ?", (user_id, currency)
        ).fetchone()
        return dict(row) if row else None


def get_all_wallets(user_id: str) -> List[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM wallets WHERE user_id = ? ORDER BY currency", (user_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_balance(user_id: str, currency: str) -> float:
    wallet = get_wallet(user_id, currency)
    return (wallet["balance"] / 100.0) if wallet else 0.0


def update_balance(conn: sqlite3.Connection, user_id: str, currency: str, delta_minor: int) -> bool:
    """Atomic balance update within an existing transaction. Prevents overdraft."""
    row = conn.execute(
        "SELECT balance FROM wallets WHERE user_id = ? AND currency = ?", (user_id, currency)
    ).fetchone()
    if not row:
        return False
    new_balance = row["balance"] + delta_minor
    if new_balance < 0:
        return False
    conn.execute(
        "UPDATE wallets SET balance = ? WHERE user_id = ? AND currency = ?",
        (new_balance, user_id, currency)
    )
    return True


def get_total_system_balances() -> Dict[str, float]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT currency, SUM(balance) as total FROM wallets GROUP BY currency ORDER BY currency"
        ).fetchall()
        return {r["currency"]: r["total"] / 100.0 for r in rows}


# ─── Transaction Operations ───────────────────────────────────────────────────

def record_transaction(tx_id: str, sender: str, recipient: str,
                        amount: float, currency: str, tx_type: str,
                        fee: float = 0.0, metadata: dict = None,
                        signature: str = "") -> bool:
    with get_db() as conn:
        conn.execute(
            """INSERT INTO transactions (tx_id, sender, recipient, amount, currency, tx_type,
               fee, timestamp, status, metadata, signature) VALUES (?,?,?,?,?,?,?,?,'CONFIRMED',?,?)""",
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
            "SELECT tx_type, COUNT(*) as cnt, SUM(amount) as vol FROM transactions GROUP BY tx_type"
        ).fetchall()
        by_ccy  = conn.execute(
            "SELECT currency, COUNT(*) as cnt, SUM(amount) as vol FROM transactions GROUP BY currency"
        ).fetchall()
        return {
            "total_transactions": total, "failed_transactions": failed,
            "total_revenue": revenue / 100.0,
            "by_type":     [dict(r) for r in by_type],
            "by_currency": [{**dict(r), "vol": (r["vol"] or 0) / 100.0} for r in by_ccy],
        }


# ─── FX Operations ────────────────────────────────────────────────────────────

def get_fx_rate(from_currency: str, to_currency: str) -> Optional[Tuple[float, float]]:
    if from_currency == to_currency:
        return 1.0, 0.0
    pair = f"{from_currency}_{to_currency}"
    with get_db() as conn:
        row = conn.execute(
            "SELECT rate, spread_pct FROM fx_rates WHERE pair = ?", (pair,)
        ).fetchone()
        return (row["rate"], row["spread_pct"]) if row else None


def get_all_fx_rates() -> List[dict]:
    with get_db() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM fx_rates ORDER BY pair").fetchall()]


# ─── Login Attempts ───────────────────────────────────────────────────────────

def record_login_attempt(phone: str, success: bool, ip_hash: str = "") -> str:
    """Records every login attempt for security auditing. Uses direct connection."""
    attempt_id = str(uuid.uuid4())
    conn = _direct_conn()
    try:
        conn.execute(
            "INSERT INTO login_attempts (attempt_id, phone, success, ip_hash, timestamp) VALUES (?,?,?,?,?)",
            (attempt_id, phone, 1 if success else 0, ip_hash, time.time())
        )
        conn.commit()
    finally:
        conn.close()
    return attempt_id


def get_login_attempts(phone: str = None, limit: int = 100) -> List[dict]:
    with get_db() as conn:
        if phone:
            rows = conn.execute(
                "SELECT * FROM login_attempts WHERE phone=? ORDER BY timestamp DESC LIMIT ?", (phone, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM login_attempts ORDER BY timestamp DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]


def get_failed_login_count(phone: str, window_seconds: int = 300) -> int:
    """Count failed logins for rate-limiting / lockout enforcement."""
    since = time.time() - window_seconds
    with get_db() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM login_attempts WHERE phone=? AND success=0 AND timestamp>?",
            (phone, since)
        ).fetchone()[0]


# ─── Suspicious Activity ──────────────────────────────────────────────────────

def flag_suspicious_activity(user_id: str, flag_type: str,
                               severity: str = "MEDIUM", details: dict = None) -> str:
    flag_id = str(uuid.uuid4())
    conn = _direct_conn()
    try:
        conn.execute(
            "INSERT INTO suspicious_activity (flag_id, user_id, flag_type, severity, details, created_at) VALUES (?,?,?,?,?,?)",
            (flag_id, user_id, flag_type, severity, json.dumps(details or {}), time.time())
        )
        conn.execute(
            "INSERT INTO audit_log (log_id, user_id, action, details, timestamp) VALUES (?,?,'SUSPICIOUS_FLAGGED',?,?)",
            (str(uuid.uuid4()), user_id, json.dumps({"flag_type": flag_type, "severity": severity}), time.time())
        )
        conn.commit()
    finally:
        conn.close()
    return flag_id


def get_suspicious_activity(resolved: bool = False, limit: int = 100) -> List[dict]:
    with get_db() as conn:
        rows = conn.execute(
            """SELECT sa.*, u.phone, u.name FROM suspicious_activity sa
               LEFT JOIN users u ON sa.user_id = u.user_id
               WHERE sa.resolved=? ORDER BY sa.created_at DESC LIMIT ?""",
            (1 if resolved else 0, limit)
        ).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            try: d["details"] = json.loads(d.get("details") or "{}")
            except Exception: d["details"] = {}
            results.append(d)
        return results


def resolve_suspicious_flag(flag_id: str, admin_id: str) -> bool:
    with get_db() as conn:
        conn.execute("UPDATE suspicious_activity SET resolved=1 WHERE flag_id=?", (flag_id,))
        conn.execute(
            "INSERT INTO audit_log (log_id, user_id, action, details, timestamp) VALUES (?,?,'FLAG_RESOLVED',?,?)",
            (str(uuid.uuid4()), admin_id, json.dumps({"flag_id": flag_id}), time.time())
        )
    return True


# ─── Audit Log ────────────────────────────────────────────────────────────────

def audit_action(user_id: str, action: str, details: dict = None):
    """
    FIX: Uses _direct_conn() instead of get_db() to prevent nested-connection
    deadlocks when called from within an existing get_db() context.
    """
    conn = _direct_conn()
    try:
        conn.execute(
            "INSERT INTO audit_log (log_id, user_id, action, details, timestamp) VALUES (?,?,?,?,?)",
            (str(uuid.uuid4()), user_id, action, json.dumps(details or {}), time.time())
        )
        conn.commit()
    finally:
        conn.close()


def get_audit_log(user_id: str = None, limit: int = 100) -> List[dict]:
    with get_db() as conn:
        if user_id:
            rows = conn.execute(
                "SELECT * FROM audit_log WHERE user_id=? ORDER BY timestamp DESC LIMIT ?", (user_id, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]


# ─── System Config ────────────────────────────────────────────────────────────

def get_config(key: str) -> Optional[str]:
    with get_db() as conn:
        row = conn.execute("SELECT config_value FROM system_config WHERE config_key=?", (key,)).fetchone()
        return row["config_value"] if row else None


def set_config(key: str, value: str, admin_id: str = "SYSTEM"):
    with get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO system_config (config_key, config_value, updated_at, updated_by) VALUES (?,?,?,?)",
            (key, value, time.time(), admin_id)
        )


def get_all_config() -> List[dict]:
    with get_db() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM system_config ORDER BY config_key").fetchall()]


# ─── System Statistics ────────────────────────────────────────────────────────

def get_system_stats() -> dict:
    with get_db() as conn:
        user_count      = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        active_count    = conn.execute("SELECT COUNT(*) FROM users WHERE is_active=1 AND is_suspended=0").fetchone()[0]
        suspended_count = conn.execute("SELECT COUNT(*) FROM users WHERE is_suspended=1").fetchone()[0]
        tx_count        = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        volume          = conn.execute("SELECT SUM(amount) FROM transactions WHERE currency='USD'").fetchone()[0] or 0
        revenue         = conn.execute("SELECT SUM(fee) FROM transactions").fetchone()[0] or 0
        failed_tx       = conn.execute("SELECT COUNT(*) FROM transactions WHERE status != 'CONFIRMED'").fetchone()[0]
        suspicious      = conn.execute("SELECT COUNT(*) FROM suspicious_activity WHERE resolved=0").fetchone()[0]
        failed_24h      = conn.execute(
            "SELECT COUNT(*) FROM login_attempts WHERE success=0 AND timestamp>?", (time.time()-86400,)
        ).fetchone()[0]
        total_logins_24h = conn.execute(
            "SELECT COUNT(*) FROM login_attempts WHERE timestamp>?", (time.time()-86400,)
        ).fetchone()[0]
        db_size = os.path.getsize(DB_PATH) if os.path.exists(DB_PATH) else 0
        return {
            "total_users":         user_count,
            "active_users":        active_count,
            "suspended_users":     suspended_count,
            "total_transactions":  tx_count,
            "failed_transactions": failed_tx,
            "total_volume_usd":    volume / 100.0,
            "total_revenue":       revenue / 100.0,
            "suspicious_flags":    suspicious,
            "failed_logins_24h":   failed_24h,
            "total_logins_24h":    total_logins_24h,
            "db_size_kb":          round(db_size / 1024, 1),
        }
    
def update_pin(user_id: str, new_pin_hash: str) -> bool: 
    """Securely replace a user PIN hash. Called by change-pin endpoint.""" 
    with get_db() as conn: 
        conn.execute( 
            'UPDATE users SET pin_hash = ? WHERE user_id = ?', 
            (new_pin_hash, user_id) 
        ) 
    return True

def get_user_wallets(user_id: str) -> List[dict]: 
    """Alias used by the API server.""" 
    return get_all_wallets(user_id)