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
    if data.get("requires_otp"):
        return data
    _token        = data["token"]
    _current_user = data["user"]
    return data["user"]


def register(phone: str, name: str, pin: str) -> dict:
    """Register a new account. Returns {message, user_id}."""
    return _post("api/v1/auth/register",
                 {"phone": phone, "name": name, "pin": pin},
                 authenticated=False)

def verify_otp(user_id: str, otp: str) -> dict:
    """
    Step 2 of 2FA login. Submit the OTP received via SMS.
    Stores the JWT token and returns the user dict on success.
    Raises RuntimeError on invalid/expired OTP.
    """
    global _token, _current_user
    data          = _post("api/v1/auth/verify-otp",
                          {"user_id": user_id, "otp": otp},
                          authenticated=False)
    _token        = data["token"]
    _current_user = data["user"]
    return data["user"]


def resend_otp(user_id: str) -> dict:
    """
    Request a new OTP be sent to the user's phone.
    Returns {message: "..."} on success.
    """
    return _post("api/v1/auth/resend-otp",
                 {"user_id": user_id},
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

# ── M-Pesa Operations ─────────────────────────────────────────────────────────

def mpesa_deposit(phone: str, amount: float) -> dict:
    """Initiate M-Pesa STK Push deposit."""
    return _post("api/v1/mpesa/initiate", {"phone": phone, "amount": amount})


def mpesa_deposit_status(internal_ref: str) -> dict:
    """Poll M-Pesa deposit status."""
    return _get(f"api/v1/mpesa/status/{internal_ref}")


def mpesa_history() -> list:
    """User's M-Pesa deposit history."""
    return _get("api/v1/mpesa/history")["deposits"]


# ── Reversal Operations ───────────────────────────────────────────────────────

def get_reversal_eligible() -> list:
    """List transactions eligible for reversal."""
    return _get("api/v1/reversal/eligible")["eligible_transactions"]


def request_reversal(tx_id: str, reason: str = "") -> dict:
    """Submit a reversal request."""
    return _post("api/v1/reversal/request", {"tx_id": tx_id, "reason": reason})


# ── Admin Reversal Operations ─────────────────────────────────────────────────

def admin_get_reversals(include_history: bool = False) -> list:
    url = f"api/v1/admin/reversals?include_history={str(include_history).lower()}"
    return _get(url)["reversals"]


def admin_approve_reversal(reversal_id: str, note: str = "") -> dict:
    return _post(f"api/v1/admin/reversals/{reversal_id}/approve", {"note": note})


def admin_reject_reversal(reversal_id: str, note: str = "") -> dict:
    return _post(f"api/v1/admin/reversals/{reversal_id}/reject", {"note": note})


def admin_mpesa_transactions(status: str = None, limit: int = 200) -> list:
    url = f"api/v1/admin/mpesa-transactions?limit={limit}"
    if status:
        url += f"&status={status}"
    return _get(url)["transactions"]


def get_help() -> dict:
    """Fetch help panel content."""
    return _get("api/v1/help", authenticated=False)

# ── Reversal Operations ───────────────────────────────────────────────────────

def get_eligible_reversals() -> list:
    """Get transactions eligible for reversal."""
    return _get("api/v1/reversal/eligible")["eligible_transactions"]


def request_reversal(tx_id: str, reason: str = "") -> dict:
    """Request reversal for a transaction."""
    return _post("api/v1/reversal/request", {"tx_id": tx_id, "reason": reason})


def get_pending_reversals() -> list:
    """Admin: Get all pending reversal requests."""
    return _get("api/v1/admin/reversals/pending")["reversals"]


def approve_reversal(reversal_id: str, note: str = "") -> dict:
    """Admin: Approve a reversal request."""
    return _post(f"api/v1/admin/reversals/{reversal_id}/approve", {"note": note})


def reject_reversal(reversal_id: str, note: str = "") -> dict:
    """Admin: Reject a reversal request."""
    return _post(f"api/v1/admin/reversals/{reversal_id}/reject", {"note": note})


def get_reversal_history(status: str = None) -> list:
    """Admin: Get reversal history with optional status filter."""
    url = "api/v1/admin/reversals/history"
    if status:
        url += f"?status={status}"
    return _get(url)["reversals"]


def get_transaction_stats() -> dict:
    """Admin: Get transaction statistics with failure reasons."""
    return _get("api/v1/admin/transaction-stats")


def get_user_by_phone(phone: str) -> dict:
    """Get user info by phone number (for confirmation)."""
    return _get(f"api/v1/user/by-phone/{phone}", authenticated=False)


def get_notifications() -> list:
    """Get user notifications."""
    return _get("api/v1/notifications")["notifications"]


def mark_notification_read(notification_id: str) -> dict:
    """Mark notification as read."""
    return _post(f"api/v1/notifications/{notification_id}/read", {})
