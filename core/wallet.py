"""
ChainPay Wallet & FX Engine — v4.0 (Multi-Currency Fix)
========================================================
CHANGES FROM v3:
  1. convert_currency: REMOVED "only from base currency" restriction.
     Any-to-any conversion is now allowed for all supported currencies.
  2. convert_currency: Uses decimal.Decimal internally for all arithmetic
     to prevent floating-point precision errors.
  3. convert_currency: Uses BEGIN IMMEDIATE + explicit rollback guard so
     partial deductions can never occur.
  4. convert_currency: FX rate retrieval failure now returns a clear error
     instead of a generic "Service error".
  5. send_money: Recipient wallet existence is verified before debiting
     sender — ensures "Credit failed" can never cause a half-debit.
  6. send_money: Currency is no longer restricted to any single currency;
     all SUPPORTED_CURRENCIES are accepted.
  7. All methods: amounts stored/retrieved as INTEGER minor units (×100)
     and converted via Decimal to avoid float drift.
  8. Backward-compatible: all existing public signatures preserved.
"""

import uuid
import time
import random
import json
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from typing import Optional, Tuple, Dict, List

try:
    from core import database as db
    from core.blockchain import get_blockchain, Transaction
    from core.security import sign_transaction, generate_keypair
    from core.database import notify_user, get_user_by_phone_with_country
except ModuleNotFoundError:
    import database as db
    from blockchain import get_blockchain, Transaction
    from security import sign_transaction, generate_keypair
    from database import notify_user, get_user_by_phone_with_country


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

# Decimal precision: store as minor units (integer cents)
_MINOR = Decimal("0.01")

DAILY_LIMIT_USD = 5000.0
TX_LIMIT_USD    = 2000.0


# ─── Decimal helpers ──────────────────────────────────────────────────────────

def _to_decimal(value) -> Decimal:
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return Decimal("0")


def _to_minor(amount: Decimal) -> int:
    """Convert Decimal amount to integer minor units (multiply by 100, round half-up)."""
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _from_minor(minor: int) -> Decimal:
    return Decimal(minor) / Decimal("100")


# ─── FX Engine ────────────────────────────────────────────────────────────────

class FXEngine:
    """
    FX rate engine with bid/ask spreads and ±0.3% volatility.
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

        spread_pct     = Decimal("1.5")
        mid_rate_d     = _to_decimal(mid_rate)
        amount_d       = _to_decimal(amount)
        effective_rate = mid_rate_d * (1 - spread_pct / Decimal("200"))
        to_amount      = (amount_d * effective_rate).quantize(
                             Decimal("0.0001"), rounding=ROUND_HALF_UP)
        fx_fee         = (amount_d * (spread_pct / Decimal("200"))).quantize(
                             Decimal("0.000001"), rounding=ROUND_HALF_UP)

        return {
            "from_currency":  from_ccy,
            "to_currency":    to_ccy,
            "from_amount":    float(amount_d),
            "to_amount":      float(to_amount),
            "mid_rate":       round(float(mid_rate_d), 6),
            "effective_rate": round(float(effective_rate), 6),
            "spread_pct":     float(spread_pct),
            "fx_fee":         float(fx_fee),
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
    Tiered fee structure. Returns fee in USD as float (full precision, no rounding).
    Callers are responsible for converting to target currency.
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

    return amount_usd * fee_pct


# ─── Compliance Engine ────────────────────────────────────────────────────────

class ComplianceEngine:
    """
    AML/KYC transaction monitoring rule engine.
    """

    SANCTIONS_LIST = {"blocked_user_001", "blocked_user_002"}

    @classmethod
    def check_transaction(cls, user_id: str, amount_usd: float,
                           recipient_id: str, is_cross_border: bool) -> Tuple[bool, str]:
        if user_id in cls.SANCTIONS_LIST or recipient_id in cls.SANCTIONS_LIST:
            db.flag_suspicious_activity(user_id, "SANCTIONS_HIT", "CRITICAL",
                                        {"amount": amount_usd, "recipient": recipient_id})
            return False, "Transaction blocked: sanctions screening"

        user = db.get_user_by_id(user_id)
        if user and user.get("is_suspended"):
            return False, "Account suspended. Contact support."

        if amount_usd > TX_LIMIT_USD:
            return False, f"Exceeds single transaction limit (${TX_LIMIT_USD:,.2f})"

        txs = db.get_user_transactions(user_id, limit=200)
        today_start = time.time() - 86400
        daily_total = sum(
            tx["amount"] for tx in txs
            if tx["timestamp"] > today_start and tx["sender"] == user_id
        )
        if daily_total + amount_usd > DAILY_LIMIT_USD:
            return False, f"Exceeds daily limit (${DAILY_LIMIT_USD:,.2f})"

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
                   note: str = "", pin: str = None) -> Tuple[bool, str, Optional[dict]]:
        """
        Send money in ANY supported currency to recipient identified by phone.
        Recipient must have a wallet for the requested currency (auto-created at
        registration; see ensure_all_currency_wallets).
        """
        if amount <= 0:
            return False, "Amount must be positive", None
        if currency not in SUPPORTED_CURRENCIES:
            return False, f"Unsupported currency: {currency}", None

        # Get recipient
        recipient = db.get_user_by_phone_with_country(recipient_phone)
        if not recipient:
            return False, f"Recipient not found: {recipient_phone}", None
        if recipient["user_id"] == sender_id:
            return False, "Cannot send to yourself", None

        # Ensure recipient has a wallet for this currency (back-fill if needed)
        db.ensure_all_currency_wallets(recipient["user_id"])

        # Verify recipient wallet exists for requested currency
        recipient_wallet = db.get_wallet(recipient["user_id"], currency)
        if not recipient_wallet:
            return False, f"Recipient has no {currency} wallet", None

        # Verify sender balance
        balance = db.get_balance(sender_id, currency)
        amount_d = _to_decimal(amount)
        if _to_decimal(balance) < amount_d:
            return False, f"Insufficient {currency} balance (have {balance:.4f})", None

        # Convert to USD for compliance
        if currency == "USD":
            amount_usd  = float(amount_d)
            rate_to_usd = 1.0
        else:
            rate_to_usd = FXEngine.get_live_rate(currency, "USD")
            if not rate_to_usd:
                return False, f"FX rate unavailable for {currency}/USD", None
            amount_usd = float(amount_d * _to_decimal(rate_to_usd))

        sender = db.get_user_by_id(sender_id)
        is_cross_border = (sender.get('base_country', '') !=
                           recipient.get('base_country', 'Unknown'))

        allowed, reason = ComplianceEngine.check_transaction(
            sender_id, amount_usd, recipient["user_id"], is_cross_border
        )
        if not allowed:
            return False, reason, None

        # Calculate fee
        fee_usd         = calculate_fee(amount_usd, is_cross_border)
        fee_d           = (_to_decimal(fee_usd) / _to_decimal(rate_to_usd)
                           if rate_to_usd else Decimal("0"))
        fee_d           = max(fee_d, Decimal("0.01")) if fee_usd > 0 else Decimal("0")
        total_debit_d   = amount_d + fee_d

        if _to_decimal(balance) < total_debit_d:
            return False, (f"Insufficient funds "
                           f"(need {float(total_debit_d):.4f} {currency} including fee)"), None

        tx_id     = str(uuid.uuid4())
        signature = sign_transaction(
            f"{tx_id}:{sender_id}:{recipient['user_id']}:{amount}:{currency}",
            sender["private_key"]
        )

        total_debit_minor = _to_minor(total_debit_d)
        amount_minor      = _to_minor(amount_d)
        fee_minor         = _to_minor(fee_d)

        try:
            with db.get_db() as conn:
                conn.execute("BEGIN IMMEDIATE")

                # Debit sender
                if not db.update_balance(conn, sender_id, currency, -total_debit_minor):
                    conn.rollback()
                    return False, "Debit failed — insufficient balance", None

                # Credit recipient
                if not db.update_balance(conn, recipient["user_id"], currency, amount_minor):
                    # Roll back the debit
                    conn.rollback()
                    return False, f"Credit failed — recipient {currency} wallet error", None

                conn.execute(
                    """INSERT INTO transactions
                       (tx_id, sender, recipient, amount, currency,
                        tx_type, fee, timestamp, status, metadata, signature)
                       VALUES (?,?,?,?,?,'SEND',?,?,'CONFIRMED',?,?)""",
                    (tx_id, sender_id, recipient["user_id"],
                     amount_minor, currency, fee_minor, time.time(),
                     json.dumps({
                         "note": note,
                         "recipient_phone": recipient_phone,
                         "is_cross_border": is_cross_border,
                     }),
                     signature)
                )

                conn.commit()
        except Exception as e:
            return False, f"Transaction failed: {e}", None

        # Record on blockchain (non-critical)
        try:
            blockchain = get_blockchain()
            blockchain.add_transaction(Transaction(
                tx_id=tx_id, sender=sender_id, recipient=recipient["user_id"],
                amount=float(amount_d), currency=currency, tx_type="SEND",
                fee=float(fee_d), timestamp=time.time(),
                metadata={"note": note},
                signature=signature
            ))
        except Exception:
            pass

        db.audit_action(sender_id, "SEND_MONEY", {
            "tx_id": tx_id, "amount": float(amount_d), "currency": currency,
            "recipient": recipient_phone, "fee": float(fee_d),
            "is_cross_border": is_cross_border
        })

        try:
            notify_user(
                recipient["user_id"],
                "TRANSFER_RECEIVED",
                f"You received {format_amount(float(amount_d), currency)} from {sender['name']}",
                {"tx_id": tx_id, "amount": float(amount_d), "currency": currency,
                 "sender_name": sender['name']}
            )
        except Exception:
            pass

        return True, "Transfer successful", {
            "tx_id":           tx_id,
            "recipient_name":  recipient["name"],
            "recipient_phone": recipient_phone,
            "amount":          float(amount_d),
            "currency":        currency,
            "fee":             float(fee_d),
            "total_deducted":  float(total_debit_d),
            "timestamp":       time.time(),
            "status":          "CONFIRMED",
            "is_cross_border": is_cross_border,
        }

    @staticmethod
    def convert_currency(user_id: str, from_ccy: str, to_ccy: str,
                          amount: float) -> Tuple[bool, str, Optional[dict]]:
        """
        Convert between any two supported currencies.
        Restrictions:
          - from_ccy != to_ccy
          - Both currencies must be in SUPPORTED_CURRENCIES
          - User must have sufficient from_ccy balance

        The "only from base currency" restriction is REMOVED.
        Uses Decimal arithmetic to avoid float precision errors.
        Atomic: uses BEGIN IMMEDIATE; rolls back on any error.
        """
        if from_ccy == to_ccy:
            return False, "Cannot convert same currency", None
        if amount <= 0:
            return False, "Amount must be positive", None
        if from_ccy not in SUPPORTED_CURRENCIES:
            return False, f"Unsupported source currency: {from_ccy}", None
        if to_ccy not in SUPPORTED_CURRENCIES:
            return False, f"Unsupported target currency: {to_ccy}", None

        amount_d = _to_decimal(amount)

        # Ensure wallets exist for both currencies
        db.ensure_all_currency_wallets(user_id)

        # Check source balance
        balance = db.get_balance(user_id, from_ccy)
        balance_d = _to_decimal(balance)
        if balance_d < amount_d:
            return False, f"Insufficient {from_ccy} balance ({balance:.4f} available)", None

        # Get FX quote — this is the only point where rate retrieval can fail
        quote = FXEngine.get_conversion_quote(from_ccy, to_ccy, float(amount_d))
        if not quote:
            return False, (
                f"FX rate not available for {from_ccy}/{to_ccy}. "
                f"Check server FX table or try again later."
            ), None

        to_amount_d    = _to_decimal(quote["to_amount"])
        fx_fee_d       = _to_decimal(quote["fx_fee"])
        from_minor     = _to_minor(amount_d)
        to_minor       = _to_minor(to_amount_d)
        fee_minor      = _to_minor(fx_fee_d)

        tx_id = str(uuid.uuid4())

        # ── Atomic conversion ──────────────────────────────────────────────
        conn = db._direct_conn()
        try:
            conn.execute("BEGIN IMMEDIATE")

            # Re-verify balance inside the lock
            row = conn.execute(
                "SELECT balance, locked_balance FROM wallets WHERE user_id=? AND currency=?",
                (user_id, from_ccy)
            ).fetchone()
            if not row:
                conn.rollback()
                return False, f"Source wallet ({from_ccy}) not found", None

            locked   = row["locked_balance"] or 0
            spendable = row["balance"] - locked
            if spendable < from_minor:
                conn.rollback()
                return False, (
                    f"Insufficient {from_ccy} balance inside transaction "
                    f"(have {spendable/100:.4f}, need {float(amount_d):.4f})"
                ), None

            # Deduct source
            new_from = row["balance"] - from_minor
            conn.execute(
                "UPDATE wallets SET balance=?, updated_at=? WHERE user_id=? AND currency=?",
                (new_from, time.time(), user_id, from_ccy)
            )

            # Verify target wallet exists
            target_row = conn.execute(
                "SELECT balance FROM wallets WHERE user_id=? AND currency=?",
                (user_id, to_ccy)
            ).fetchone()
            if not target_row:
                conn.rollback()
                return False, f"Target wallet ({to_ccy}) not found", None

            # Credit target
            new_to = target_row["balance"] + to_minor
            conn.execute(
                "UPDATE wallets SET balance=?, updated_at=? WHERE user_id=? AND currency=?",
                (new_to, time.time(), user_id, to_ccy)
            )

            # Record conversion transaction
            conn.execute(
                """INSERT INTO transactions
                   (tx_id, sender, recipient, amount, currency,
                    tx_type, fee, timestamp, status, metadata)
                   VALUES (?,?,?,?,?,'FX_CONVERT',?,?,'CONFIRMED',?)""",
                (tx_id, user_id, user_id, from_minor, from_ccy,
                 fee_minor, time.time(),
                 json.dumps({
                     "from_currency": from_ccy,
                     "to_currency":   to_ccy,
                     "to_amount":     float(to_amount_d),
                     "rate":          quote["effective_rate"],
                     "mid_rate":      quote["mid_rate"],
                     "spread_pct":    quote["spread_pct"],
                 }))
            )

            conn.commit()
        except Exception as e:
            conn.rollback()
            return False, f"Conversion failed: {e}", None
        finally:
            conn.close()

        # Non-critical: blockchain + audit
        try:
            blockchain = get_blockchain()
            blockchain.add_transaction(Transaction(
                tx_id=tx_id, sender=user_id, recipient=user_id,
                amount=float(amount_d), currency=from_ccy, tx_type="FX_CONVERT",
                fee=float(fx_fee_d), timestamp=time.time(), metadata=quote
            ))
        except Exception:
            pass

        db.audit_action(user_id, "FX_CONVERT", {
            "from": from_ccy, "to": to_ccy,
            "amount": float(amount_d), "received": float(to_amount_d),
            "rate": quote["effective_rate"]
        })

        return True, "Conversion successful", {**quote, "tx_id": tx_id}

    @staticmethod
    def deposit(user_id: str, amount: float, currency: str,
                method: str = "BANK_TRANSFER") -> Tuple[bool, str, Optional[dict]]:
        if amount <= 0:
            return False, "Amount must be positive", None
        if amount > 10000:
            return False, "Single deposit limit: 10,000 equivalent", None
        if currency not in SUPPORTED_CURRENCIES:
            return False, f"Unsupported currency: {currency}", None

        # Ensure wallet row exists
        db.ensure_all_currency_wallets(user_id)

        amount_d = _to_decimal(amount)
        minor    = _to_minor(amount_d)
        tx_id    = str(uuid.uuid4())

        try:
            with db.get_db() as conn:
                if not db.update_balance(conn, user_id, currency, minor):
                    return False, "Deposit failed — wallet not found", None
                conn.execute(
                    """INSERT INTO transactions
                       (tx_id, sender, recipient, amount, currency,
                        tx_type, fee, timestamp, status, metadata)
                       VALUES (?,'SYSTEM',?,?,?,'DEPOSIT',0,?,'CONFIRMED',?)""",
                    (tx_id, user_id, minor, currency,
                     time.time(), json.dumps({"method": method}))
                )
        except Exception as e:
            return False, f"Deposit failed: {e}", None

        try:
            blockchain = get_blockchain()
            blockchain.add_transaction(Transaction(
                tx_id=tx_id, sender="SYSTEM", recipient=user_id,
                amount=float(amount_d), currency=currency, tx_type="DEPOSIT",
                fee=0.0, timestamp=time.time()
            ))
        except Exception:
            pass

        return True, "Deposit successful", {
            "tx_id":    tx_id,
            "amount":   float(amount_d),
            "currency": currency,
            "method":   method
        }

    @staticmethod
    def withdraw(user_id: str, amount: float, currency: str,
                 method: str = "BANK_TRANSFER") -> Tuple[bool, str, Optional[dict]]:
        if amount <= 0:
            return False, "Amount must be positive", None
        if currency not in SUPPORTED_CURRENCIES:
            return False, f"Unsupported currency: {currency}", None

        amount_d = _to_decimal(amount)
        balance  = db.get_balance(user_id, currency)
        if _to_decimal(balance) < amount_d:
            return False, f"Insufficient {currency} balance (have {balance:.4f})", None

        rate_to_usd = FXEngine.get_live_rate(currency, "USD") or 1.0
        amount_usd  = float(amount_d * _to_decimal(rate_to_usd))
        fee_usd     = calculate_fee(amount_usd)
        fee_d       = _to_decimal(fee_usd) / _to_decimal(rate_to_usd) if rate_to_usd else Decimal("0")
        fee_d       = max(fee_d, Decimal("0.01")) if fee_usd > 0 else Decimal("0")
        total_d     = amount_d + fee_d

        if _to_decimal(balance) < total_d:
            return False, "Insufficient funds including withdrawal fee", None

        total_minor = _to_minor(total_d)
        fee_minor   = _to_minor(fee_d)
        amt_minor   = _to_minor(amount_d)

        tx_id = str(uuid.uuid4())
        try:
            with db.get_db() as conn:
                if not db.update_balance(conn, user_id, currency, -total_minor):
                    return False, "Withdrawal failed", None
                conn.execute(
                    """INSERT INTO transactions
                       (tx_id, sender, recipient, amount, currency,
                        tx_type, fee, timestamp, status, metadata)
                       VALUES (?,?,'SYSTEM',?,?,'WITHDRAW',?,?,'CONFIRMED',?)""",
                    (tx_id, user_id, amt_minor, currency,
                     fee_minor, time.time(), json.dumps({"method": method}))
                )
        except Exception as e:
            return False, f"Withdrawal failed: {e}", None

        db.audit_action(user_id, "WITHDRAW", {
            "tx_id": tx_id, "amount": float(amount_d),
            "fee": float(fee_d), "currency": currency
        })

        return True, "Withdrawal processed", {
            "tx_id":    tx_id,
            "amount":   float(amount_d),
            "fee":      float(fee_d),
            "currency": currency,
            "method":   method
        }


# ─── Helpers ──────────────────────────────────────────────────────────────────

def format_amount(amount: float, currency: str) -> str:
    symbol = CURRENCY_SYMBOLS.get(currency, currency + " ")
    if currency in ("KES", "NGN"):
        return f"{symbol}{amount:,.0f}"
    return f"{symbol}{amount:,.2f}"