"""
ChainPay Wallet & FX Engine — Fixed
=====================================
FIX LOG:
  - calculate_fee: round(x, 2) was zeroing sub-dollar fees.
    Fixed to round(x, 6), then convert to currency unit.
    Added minimum fee of 1 minor unit to prevent zero-fee transactions.
  - send_money: fee_in_currency conversion now uses explicit division by rate
    rather than ratio multiplication (same result but clearer and safer).
  - All UI-callable methods wrapped to propagate meaningful error strings.
  - ComplianceEngine now writes to suspicious_activity table via DB function.
"""

import uuid
import time
import random
import json
from typing import Optional, Tuple, Dict, List
from core import database as db
from core.blockchain import get_blockchain, Transaction
from core.security import sign_transaction, generate_keypair


SUPPORTED_CURRENCIES = ["USD", "EUR", "KES", "NGN", "GBP"]

CURRENCY_SYMBOLS = {
    "USD": "$", "EUR": "€", "KES": "KES ",
    "NGN": "\u20a6", "GBP": "\u00a3"
}

CURRENCY_NAMES = {
    "USD": "US Dollar",      "EUR": "Euro",
    "KES": "Kenyan Shilling","NGN": "Nigerian Naira",
    "GBP": "British Pound"
}

DAILY_LIMIT_USD   = 5000.0
TX_LIMIT_USD      = 2000.0


# ─── FX Engine ────────────────────────────────────────────────────────────────

class FXEngine:
    """
    Simulated FX rate engine with bid/ask spreads and ±0.3% volatility.
    Rates cached for 30 seconds per pair. Cross-rates computed via USD pivot.
    """

    _rate_cache: Dict[str, Tuple[float, float]] = {}
    CACHE_TTL = 30

    @classmethod
    def get_live_rate(cls, from_ccy: str, to_ccy: str) -> Optional[float]:
        if from_ccy == to_ccy:
            return 1.0
        cache_key = f"{from_ccy}_{to_ccy}"
        cached = cls._rate_cache.get(cache_key)
        if cached and time.time() - cached[1] < cls.CACHE_TTL:
            return cached[0]

        result = db.get_fx_rate(from_ccy, to_ccy)
        if result:
            base_rate, _ = result
        else:
            # Cross-rate via USD pivot
            from_usd = db.get_fx_rate(from_ccy, "USD")
            usd_to   = db.get_fx_rate("USD", to_ccy)
            if not from_usd or not usd_to:
                return None
            base_rate = from_usd[0] * usd_to[0]

        # Simulate market micro-volatility: ±0.3% random walk
        volatility = random.uniform(-0.003, 0.003)
        live_rate  = base_rate * (1 + volatility)
        cls._rate_cache[cache_key] = (live_rate, time.time())
        return live_rate

    @classmethod
    def get_conversion_quote(cls, from_ccy: str, to_ccy: str,
                              amount: float) -> Optional[dict]:
        mid_rate = cls.get_live_rate(from_ccy, to_ccy)
        if mid_rate is None:
            return None
        spread_pct     = 1.5
        effective_rate = mid_rate * (1 - spread_pct / 200)
        to_amount      = round(amount * effective_rate, 6)
        fx_fee         = round(amount * (spread_pct / 200), 6)
        return {
            "from_currency":  from_ccy,
            "to_currency":    to_ccy,
            "from_amount":    amount,
            "to_amount":      round(to_amount, 4),
            "mid_rate":       round(mid_rate, 6),
            "effective_rate": round(effective_rate, 6),
            "spread_pct":     spread_pct,
            "fx_fee":         round(fx_fee, 6),
            "valid_for_seconds": cls.CACHE_TTL,
        }

    @classmethod
    def get_rate_table(cls) -> List[dict]:
        pairs = []
        for from_ccy in SUPPORTED_CURRENCIES:
            for to_ccy in SUPPORTED_CURRENCIES:
                if from_ccy != to_ccy:
                    rate = cls.get_live_rate(from_ccy, to_ccy)
                    if rate:
                        pairs.append({"pair": f"{from_ccy}/{to_ccy}",
                                      "rate": rate, "from": from_ccy, "to": to_ccy})
        return pairs


# ─── Fee Calculator ───────────────────────────────────────────────────────────

def calculate_fee(amount_usd: float, is_cross_border: bool = False) -> float:
    """
    Tiered fee structure. O(1).

    FIX: Previously used round(x, 2) which zeroed all sub-$1 fees.
    Now returns full-precision float in USD.
    Callers are responsible for converting to target currency.

    Returns fee in USD as float (full precision, no rounding).
    """
    if amount_usd <= 0:
        return 0.0
    if amount_usd < 10:
        fee_pct = 0.005
    elif amount_usd < 100:
        fee_pct = 0.010
    elif amount_usd < 1000:
        fee_pct = 0.015
    else:
        fee_pct = 0.020

    if is_cross_border:
        fee_pct += 0.005

    return amount_usd * fee_pct  # No round() — callers convert to minor units


# ─── Compliance Engine ────────────────────────────────────────────────────────

class ComplianceEngine:
    """
    AML/KYC transaction monitoring rule engine.
    Checks: single TX limit, daily limit, velocity, structuring, sanctions.
    Writes findings to suspicious_activity table via db.flag_suspicious_activity().
    """

    SANCTIONS_LIST = {"blocked_user_001", "blocked_user_002"}

    @classmethod
    def check_transaction(cls, user_id: str, amount_usd: float,
                           recipient_id: str, is_cross_border: bool) -> Tuple[bool, str]:
        # Sanctions screening
        if user_id in cls.SANCTIONS_LIST or recipient_id in cls.SANCTIONS_LIST:
            db.flag_suspicious_activity(user_id, "SANCTIONS_HIT", "CRITICAL",
                                        {"amount": amount_usd, "recipient": recipient_id})
            return False, "Transaction blocked: sanctions screening"

        # Suspended user check
        user = db.get_user_by_id(user_id)
        if user and user.get("is_suspended"):
            return False, "Account suspended. Contact support."

        # Single TX limit
        if amount_usd > TX_LIMIT_USD:
            return False, f"Exceeds single transaction limit (${TX_LIMIT_USD:,.2f})"

        # Daily limit
        txs = db.get_user_transactions(user_id, limit=200)
        today_start  = time.time() - 86400
        daily_total  = sum(
            tx["amount"] for tx in txs
            if tx["timestamp"] > today_start and tx["sender"] == user_id
        )
        # Convert daily total to USD for comparison
        # (simplified: assume already in USD for compliance check; full impl would use FX)
        if daily_total + amount_usd > DAILY_LIMIT_USD:
            return False, f"Exceeds daily limit (${DAILY_LIMIT_USD:,.2f})"

        # Structuring detection: ≥3 transactions in range $900–$1000 within 24h
        recent_structured = [
            tx for tx in txs
            if tx["timestamp"] > today_start
            and tx["sender"] == user_id
            and 900 <= tx["amount"] <= 1000
        ]
        if len(recent_structured) >= 3:
            db.flag_suspicious_activity(user_id, "STRUCTURING", "HIGH",
                                        {"tx_count": len(recent_structured)})
            return False, "Suspicious activity detected: transaction structuring"

        # Velocity check: >10 transactions in 1 hour
        hour_ago     = time.time() - 3600
        hourly_count = sum(1 for tx in txs
                          if tx["timestamp"] > hour_ago and tx["sender"] == user_id)
        if hourly_count >= 10:
            db.flag_suspicious_activity(user_id, "VELOCITY", "MEDIUM",
                                        {"tx_count_1h": hourly_count})
            return False, "Rate limit: too many transactions"

        return True, "OK"


# ─── Wallet Service ───────────────────────────────────────────────────────────

class WalletService:
    """
    Core wallet operations. All financial mutations are atomic DB transactions.
    Pattern: validate → compliance → debit → credit → record → blockchain → audit.
    """

    @staticmethod
    def send_money(sender_id: str, recipient_phone: str,
                   amount: float, currency: str,
                   note: str = "") -> Tuple[bool, str, Optional[dict]]:
        """
        Send money to recipient identified by phone number.

        FIX: fee_in_currency now correctly converts USD fee to target currency
        using division by USD rate, not multiplication by amount ratio.
        FIX: fee stored as round(fee * 100) not int(fee * 100) — prevents zero-fee bug.
        """
        # Validate inputs
        if amount <= 0:
            return False, "Amount must be positive", None

        recipient = db.get_user_by_phone(recipient_phone)
        if not recipient:
            return False, f"Recipient not found: {recipient_phone}", None
        if recipient["user_id"] == sender_id:
            return False, "Cannot send to yourself", None

        balance = db.get_balance(sender_id, currency)
        if balance < amount:
            return False, f"Insufficient {currency} balance (have {balance:.2f})", None

        # Convert to USD for compliance
        if currency == "USD":
            amount_usd = amount
            rate_to_usd = 1.0
        else:
            rate_to_usd = FXEngine.get_live_rate(currency, "USD")
            if not rate_to_usd:
                return False, f"FX rate unavailable for {currency}", None
            amount_usd = amount * rate_to_usd

        is_cross_border = False
        allowed, reason = ComplianceEngine.check_transaction(
            sender_id, amount_usd, recipient["user_id"], is_cross_border
        )
        if not allowed:
            return False, reason, None

        # FIX: Calculate fee in USD, convert to source currency
        fee_usd        = calculate_fee(amount_usd, is_cross_border)
        fee_in_currency = fee_usd / rate_to_usd  # Convert USD fee to source currency

        # Minimum fee: 1 minor unit (0.01 of currency)
        fee_in_currency = max(fee_in_currency, 0.01) if fee_usd > 0 else 0.0

        total_debit = amount + fee_in_currency

        if balance < total_debit:
            return False, (f"Insufficient funds "
                           f"(need {total_debit:.4f} {currency} including fee)"), None

        tx_id  = str(uuid.uuid4())
        sender = db.get_user_by_id(sender_id)
        signature = sign_transaction(
            f"{tx_id}:{sender_id}:{recipient['user_id']}:{amount}:{currency}",
            sender["private_key"]
        )

        try:
            with db.get_db() as conn:
                if not db.update_balance(conn, sender_id, currency, -round(total_debit * 100)):
                    return False, "Debit failed — insufficient balance", None
                if not db.update_balance(conn, recipient["user_id"], currency, round(amount * 100)):
                    return False, "Credit failed", None
                conn.execute(
                    """INSERT INTO transactions (tx_id, sender, recipient, amount, currency,
                       tx_type, fee, timestamp, status, metadata, signature)
                       VALUES (?,?,?,?,?,'SEND',?,?,'CONFIRMED',?,?)""",
                    (tx_id, sender_id, recipient["user_id"],
                     round(amount * 100), currency,
                     round(fee_in_currency * 100), time.time(),
                     json.dumps({"note": note, "recipient_phone": recipient_phone}),
                     signature)
                )
        except Exception as e:
            return False, f"Transaction failed: {e}", None

        # Record on blockchain
        blockchain = get_blockchain()
        blockchain.add_transaction(Transaction(
            tx_id=tx_id, sender=sender_id, recipient=recipient["user_id"],
            amount=amount, currency=currency, tx_type="SEND",
            fee=fee_in_currency, timestamp=time.time(),
            metadata={"note": note}, signature=signature
        ))

        db.audit_action(sender_id, "SEND_MONEY", {
            "tx_id": tx_id, "amount": amount, "currency": currency,
            "recipient": recipient_phone, "fee": fee_in_currency
        })

        return True, "Transfer successful", {
            "tx_id":            tx_id,
            "recipient_name":   recipient["name"],
            "recipient_phone":  recipient_phone,
            "amount":           amount,
            "currency":         currency,
            "fee":              round(fee_in_currency, 6),
            "total_deducted":   round(total_debit, 6),
            "timestamp":        time.time(),
            "status":           "CONFIRMED",
        }

    @staticmethod
    def convert_currency(user_id: str, from_ccy: str, to_ccy: str,
                          amount: float) -> Tuple[bool, str, Optional[dict]]:
        if from_ccy == to_ccy:
            return False, "Cannot convert same currency", None
        if amount <= 0:
            return False, "Amount must be positive", None

        balance = db.get_balance(user_id, from_ccy)
        if balance < amount:
            return False, f"Insufficient {from_ccy} balance", None

        quote = FXEngine.get_conversion_quote(from_ccy, to_ccy, amount)
        if not quote:
            return False, f"FX rate not available for {from_ccy}/{to_ccy}", None

        to_amount = quote["to_amount"]
        tx_id     = str(uuid.uuid4())

        try:
            with db.get_db() as conn:
                if not db.update_balance(conn, user_id, from_ccy, -round(amount * 100)):
                    return False, "Debit failed", None
                if not db.update_balance(conn, user_id, to_ccy, round(to_amount * 100)):
                    return False, "Credit failed", None
                conn.execute(
                    """INSERT INTO transactions (tx_id, sender, recipient, amount, currency,
                       tx_type, fee, timestamp, status, metadata)
                       VALUES (?,?,?,?,?,'FX_CONVERT',?,?,'CONFIRMED',?)""",
                    (tx_id, user_id, user_id, round(amount * 100), from_ccy,
                     round(quote["fx_fee"] * 100), time.time(),
                     json.dumps({
                         "from_currency": from_ccy, "to_currency": to_ccy,
                         "to_amount": to_amount, "rate": quote["effective_rate"]
                     }))
                )
        except Exception as e:
            return False, f"Conversion failed: {e}", None

        blockchain = get_blockchain()
        blockchain.add_transaction(Transaction(
            tx_id=tx_id, sender=user_id, recipient=user_id,
            amount=amount, currency=from_ccy, tx_type="FX_CONVERT",
            fee=quote["fx_fee"], timestamp=time.time(), metadata=quote
        ))

        db.audit_action(user_id, "FX_CONVERT", {
            "from": from_ccy, "to": to_ccy,
            "amount": amount, "received": to_amount
        })

        return True, "Conversion successful", {**quote, "tx_id": tx_id}

    @staticmethod
    def deposit(user_id: str, amount: float, currency: str,
                method: str = "BANK_TRANSFER") -> Tuple[bool, str, Optional[dict]]:
        if amount <= 0:
            return False, "Amount must be positive", None
        if amount > 10000:
            return False, "Single deposit limit: 10,000 equivalent", None

        tx_id = str(uuid.uuid4())
        try:
            with db.get_db() as conn:
                if not db.update_balance(conn, user_id, currency, round(amount * 100)):
                    return False, "Deposit failed — wallet not found", None
                conn.execute(
                    """INSERT INTO transactions (tx_id, sender, recipient, amount, currency,
                       tx_type, fee, timestamp, status, metadata)
                       VALUES (?,'SYSTEM',?,?,?,'DEPOSIT',0,?,'CONFIRMED',?)""",
                    (tx_id, user_id, round(amount * 100), currency,
                     time.time(), json.dumps({"method": method}))
                )
        except Exception as e:
            return False, f"Deposit failed: {e}", None

        blockchain = get_blockchain()
        blockchain.add_transaction(Transaction(
            tx_id=tx_id, sender="SYSTEM", recipient=user_id,
            amount=amount, currency=currency, tx_type="DEPOSIT",
            fee=0.0, timestamp=time.time()
        ))

        return True, "Deposit successful", {
            "tx_id": tx_id, "amount": amount,
            "currency": currency, "method": method
        }

    @staticmethod
    def withdraw(user_id: str, amount: float, currency: str,
                 method: str = "BANK_TRANSFER") -> Tuple[bool, str, Optional[dict]]:
        if amount <= 0:
            return False, "Amount must be positive", None

        balance = db.get_balance(user_id, currency)
        if balance < amount:
            return False, f"Insufficient {currency} balance (have {balance:.2f})", None

        # Small withdrawal fee: flat 0.5%
        rate_to_usd  = FXEngine.get_live_rate(currency, "USD") or 1.0
        amount_usd   = amount * rate_to_usd
        fee_usd      = calculate_fee(amount_usd)
        fee          = fee_usd / rate_to_usd
        fee          = max(fee, 0.01) if fee_usd > 0 else 0.0
        total        = amount + fee

        if balance < total:
            return False, "Insufficient funds including withdrawal fee", None

        tx_id = str(uuid.uuid4())
        try:
            with db.get_db() as conn:
                if not db.update_balance(conn, user_id, currency, -round(total * 100)):
                    return False, "Withdrawal failed", None
                conn.execute(
                    """INSERT INTO transactions (tx_id, sender, recipient, amount, currency,
                       tx_type, fee, timestamp, status, metadata)
                       VALUES (?,?,'SYSTEM',?,?,'WITHDRAW',?,?,'CONFIRMED',?)""",
                    (tx_id, user_id, round(amount * 100), currency,
                     round(fee * 100), time.time(), json.dumps({"method": method}))
                )
        except Exception as e:
            return False, f"Withdrawal failed: {e}", None

        db.audit_action(user_id, "WITHDRAW", {
            "tx_id": tx_id, "amount": amount, "fee": fee, "currency": currency
        })

        return True, "Withdrawal processed", {
            "tx_id": tx_id, "amount": amount,
            "fee": round(fee, 6), "currency": currency, "method": method
        }


def format_amount(amount: float, currency: str) -> str:
    symbol = CURRENCY_SYMBOLS.get(currency, currency + " ")
    if currency in ("KES", "NGN"):
        return f"{symbol}{amount:,.0f}"
    return f"{symbol}{amount:,.2f}"