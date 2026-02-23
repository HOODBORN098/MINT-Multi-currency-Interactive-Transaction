"""
ChainPay API Client
====================
Replaces direct database.py and wallet.py calls in the desktop GUI.
All operations go through the central REST API server.

Thread-safe: stores token in module-level variable.
All methods raise RuntimeError on API failure — callers catch and display.
"""

import json
import urllib.request
import urllib.error
import urllib.parse
import ssl
from typing import Optional, Dict, Any

from config import CONFIG, get_api_url


# ── Module-level state ────────────────────────────────────────────────────────
_token: Optional[str] = None
_current_user: Optional[dict] = None


def _get_ssl_context():
    """Build SSL context — skip verification for self-signed certs if configured."""
    if not CONFIG.get("verify_ssl", False):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode    = ssl.CERT_NONE
        return ctx
    return ssl.create_default_context()


def _request(method: str, path: str, body: dict = None,
             authenticated: bool = True) -> dict:
    """
    Core HTTP request helper using stdlib urllib only (no httpx/requests needed).
    Raises RuntimeError with a human-readable message on any failure.
    """
    url = get_api_url(path)
    headers = {"Content-Type": "application/json"}
    if authenticated and _token:
        headers["Authorization"] = f"Bearer {_token}"

    data = json.dumps(body).encode("utf-8") if body else None
    req  = urllib.request.Request(url, data=data, headers=headers, method=method)

    try:
        ctx      = _get_ssl_context()
        with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            err_body = json.loads(e.read().decode("utf-8"))
            detail   = err_body.get("detail", str(e))
        except Exception:
            detail = str(e)
        raise RuntimeError(detail)
    except urllib.error.URLError as e:
        raise RuntimeError(
            f"Cannot connect to server at {CONFIG['api_base_url']}.\n"
            f"Make sure the server is running.\n({e.reason})"
        )
    except Exception as e:
        raise RuntimeError(f"Request failed: {e}")


def _get(path: str, authenticated: bool = True) -> dict:
    return _request("GET", path, authenticated=authenticated)


def _post(path: str, body: dict, authenticated: bool = True) -> dict:
    return _request("POST", path, body=body, authenticated=authenticated)


# ── Auth Operations ───────────────────────────────────────────────────────────

def login(phone: str, pin: str) -> dict:
    """
    Authenticate and store JWT token.
    Returns user dict with: user_id, name, phone, role.
    Raises RuntimeError on bad credentials or lockout.
    """
    global _token, _current_user
    data          = _post("api/v1/auth/login", {"phone": phone, "pin": pin},
                          authenticated=False)
    _token        = data["token"]
    _current_user = data["user"]
    return data["user"]


def register(phone: str, name: str, pin: str) -> dict:
    """Register a new account. Returns {message, user_id}."""
    return _post("api/v1/auth/register",
                 {"phone": phone, "name": name, "pin": pin},
                 authenticated=False)


def change_pin(old_pin: str, new_pin: str) -> dict:
    """Change PIN for the currently logged-in user."""
    return _post("api/v1/auth/change-pin",
                 {"old_pin": old_pin, "new_pin": new_pin})


def logout():
    """Clear local session state."""
    global _token, _current_user
    _token        = None
    _current_user = None


def is_logged_in() -> bool:
    return _token is not None


def get_current_user() -> Optional[dict]:
    return _current_user


# ── Wallet Operations ─────────────────────────────────────────────────────────

def get_balances() -> list:
    """Returns list of wallet dicts: {wallet_id, user_id, currency, balance, created_at}."""
    return _get("api/v1/wallet/balances")["wallets"]


def send_money(recipient_phone: str, amount: float,
               currency: str, note: str = "") -> dict:
    """Send money. Returns {message, transaction}."""
    return _post("api/v1/wallet/send", {
        "recipient_phone": recipient_phone,
        "amount":          amount,
        "currency":        currency,
        "note":            note,
    })


def convert_currency(from_currency: str, to_currency: str, amount: float) -> dict:
    """Convert currencies. Returns {message, conversion: {...quote data}}."""
    return _post("api/v1/wallet/convert", {
        "from_currency": from_currency,
        "to_currency":   to_currency,
        "amount":        amount,
    })


def deposit(amount: float, currency: str) -> dict:
    """Deposit funds. Returns {message, transaction}."""
    return _post("api/v1/wallet/deposit", {"amount": amount, "currency": currency})


def withdraw(amount: float, currency: str) -> dict:
    """Withdraw funds. Returns {message, transaction}."""
    return _post("api/v1/wallet/withdraw", {"amount": amount, "currency": currency})


def get_transactions(limit: int = 50) -> list:
    """Returns list of transaction dicts."""
    return _get(f"api/v1/wallet/transactions?limit={limit}")["transactions"]


# ── FX Operations ─────────────────────────────────────────────────────────────

def get_fx_rates() -> list:
    """Returns list of {pair, rate, from, to} dicts."""
    return _get("api/v1/fx/rates", authenticated=False)["rates"]


def get_fx_quote(from_currency: str, to_currency: str, amount: float) -> dict:
    """Returns a conversion quote without executing."""
    return _get(
        f"api/v1/fx/quote?from={from_currency}&to={to_currency}&amount={amount}"
    )


# ── Blockchain Operations ─────────────────────────────────────────────────────

def get_blockchain_stats() -> dict:
    """Returns {total_blocks, total_transactions, pending_transactions, chain_valid, ...}."""
    return _get("api/v1/blockchain/stats")


def get_blockchain_blocks(n: int = 20) -> list:
    """Returns list of recent block dicts."""
    return _get(f"api/v1/blockchain/blocks?n={n}")["blocks"]


def mine_block() -> dict:
    """Force-mine a new block. Returns block info."""
    return _post("api/v1/blockchain/mine", {})


def validate_chain() -> dict:
    """Returns {valid: bool, message: str}."""
    return _get("api/v1/blockchain/validate")


# ── Admin Operations (only callable if role == admin) ─────────────────────────

def admin_get_stats() -> dict:
    return _get("api/v1/admin/stats")


def admin_get_users() -> list:
    return _get("api/v1/admin/users")["users"]


def admin_get_suspicious() -> list:
    return _get("api/v1/admin/suspicious")["flags"]


def admin_get_login_attempts(limit: int = 100) -> list:
    return _get(f"api/v1/admin/login-attempts?limit={limit}")["attempts"]


def admin_get_audit_log(limit: int = 100) -> list:
    return _get(f"api/v1/admin/audit-log?limit={limit}")["log"]


def admin_get_config() -> list:
    return _get("api/v1/admin/config")["config"]


def admin_get_tx_stats() -> dict:
    return _get("api/v1/admin/tx-stats")


def admin_get_system_balances() -> dict:
    return _get("api/v1/admin/system-balances")


def admin_get_blockchain_txs(limit: int = 30) -> list:
    return _get(f"api/v1/admin/blockchain-txs?limit={limit}")["transactions"]


def admin_suspend_user(user_id: str) -> dict:
    return _post(f"api/v1/admin/users/{user_id}/suspend", {})


def admin_unsuspend_user(user_id: str) -> dict:
    return _post(f"api/v1/admin/users/{user_id}/unsuspend", {})


def admin_set_role(user_id: str, role: str) -> dict:
    return _post(f"api/v1/admin/users/{user_id}/role", {"role": role})


def admin_resolve_flag(flag_id: str) -> dict:
    return _post(f"api/v1/admin/flags/{flag_id}/resolve", {})