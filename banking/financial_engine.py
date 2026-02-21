"""
FinancialEngine – core banking operations, inherits from FindAccount.
"""
import json
import os
from datetime import datetime
from .find_account import FindAccount

class FinancialEngine(FindAccount):
    """
    Handles all user transactions: deposit, withdraw, exchange, transfer,
    credit, debit, and balance inquiry. Balances are stored in a base currency.
    """
    OVERDRAFT_LIMIT = 100  # allowed negative balance (e.g., -100 in base currency)

    def __init__(self, base_currency: str = "USD"):
        super().__init__()
        self.base_currency = base_currency
        self.balances = {}  # phone -> amount in base currency
        self.transactions = {}  # phone -> list of transactions

    def _get_base_balance(self, phone: str) -> float:
        """Internal helper to get current balance (base currency)."""
        return self.balances.get(phone, 0.0)

    def _log_transaction(self, phone: str, action: str, amount: float, currency: str, details: str = "") -> None:
        """Helper to append a transaction record for a user."""
        if phone not in self.transactions:
            self.transactions[phone] = []
        
        record = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "action": action,
            "amount": amount,
            "currency": currency,
            "details": details
        }
        self.transactions[phone].append(record)

    def deposit(self, phone: str, amount: float, currency: str) -> None:
        """User deposits cash; bank receives currency and increases user's balance."""
        # Convert to base and update user balance
        base_amount = self.to_base(amount, currency)
        self.balances[phone] = self._get_base_balance(phone) + base_amount
        # Bank receives the currency
        self.adjust_liquidity(currency, amount)
        self._log_transaction(phone, "Deposit", amount, currency)

    def withdraw(self, phone: str, amount: float, currency: str) -> None:
        """User withdraws cash; bank gives currency and decreases user's balance."""
        base_amount = self.to_base(amount, currency)
        # Check user's balance (including overdraft)
        if self._get_base_balance(phone) - base_amount < -self.OVERDRAFT_LIMIT:
            raise ValueError("Insufficient funds (overdraft limit exceeded).")
        # Check bank's liquidity
        if not self.check_liquidity(currency, amount):
            raise ValueError("Bank does not have enough liquidity for this withdrawal.")
        # Update user balance and bank liquidity
        self.balances[phone] = self._get_base_balance(phone) - base_amount
        self.adjust_liquidity(currency, -amount)
        self._log_transaction(phone, "Withdrawal", amount, currency)

    def exchange(self, phone: str, from_cur: str, to_cur: str, amount: float) -> None:
        """User exchanges an amount from one currency to another."""
        base_amount = self.to_base(amount, from_cur)
        # Check user balance (overdraft allowed)
        if self._get_base_balance(phone) - base_amount < -self.OVERDRAFT_LIMIT:
            raise ValueError("Insufficient funds for exchange.")
        # Calculate how much of the target currency the user will get
        target_amount = self.convert(amount, from_cur, to_cur)
        # Check bank has enough target currency
        if not self.check_liquidity(to_cur, target_amount):
            raise ValueError("Bank does not have enough target currency.")
        # Update user balance (deduct base amount of source currency)
        self.balances[phone] = self._get_base_balance(phone) - base_amount
        # Liquidity changes: bank receives source currency, gives target currency
        self.adjust_liquidity(from_cur, amount)
        self.adjust_liquidity(to_cur, -target_amount)
        self._log_transaction(phone, "Exchange", amount, from_cur, f"To {to_cur} ({target_amount:.2f})")

    def transfer(self, sender: str, receiver: str, amount: float, currency: str) -> None:
        """
        Transfer money from one user to another. The amount is specified in a given
        currency. Only user balances (in base currency) are updated; liquidity is
        unaffected because money stays inside the system.
        """
        base_amount = self.to_base(amount, currency)
        # Check sender's balance
        if self._get_base_balance(sender) - base_amount < -self.OVERDRAFT_LIMIT:
            raise ValueError("Insufficient funds for transfer.")
        # Update balances
        self.balances[sender] = self._get_base_balance(sender) - base_amount
        self.balances[receiver] = self._get_base_balance(receiver) + base_amount
        self._log_transaction(sender, "Transfer Out", amount, currency, f"To {receiver}")
        self._log_transaction(receiver, "Transfer In", amount, currency, f"From {sender}")

    def credit(self, phone: str, amount: float, currency: str) -> None:
        """Admin operation: bank gives money to user (decreases liquidity)."""
        base_amount = self.to_base(amount, currency)
        # Check liquidity
        if not self.check_liquidity(currency, amount):
            raise ValueError("Bank does not have enough currency to credit.")
        # Increase user balance
        self.balances[phone] = self._get_base_balance(phone) + base_amount
        # Decrease liquidity (bank gives away currency)
        self.adjust_liquidity(currency, -amount)
        self._log_transaction(phone, "Credit (Admin)", amount, currency)

    def debit(self, phone: str, amount: float, currency: str) -> None:
        """Admin operation: bank takes money from user (increases liquidity)."""
        base_amount = self.to_base(amount, currency)
        # Check user's balance with overdraft
        if self._get_base_balance(phone) - base_amount < -self.OVERDRAFT_LIMIT:
            raise ValueError("User would exceed overdraft limit.")
        # Decrease user balance
        self.balances[phone] = self._get_base_balance(phone) - base_amount
        # Increase liquidity (bank receives currency)
        self.adjust_liquidity(currency, amount)
        self._log_transaction(phone, "Debit (Admin)", amount, currency)

    def show_balance(self, phone: str, currency: str | None = None) -> float:
        """
        Return the user's balance. If currency is None or matches base_currency,
        return the base amount; otherwise convert to the requested currency.
        """
        base_bal = self._get_base_balance(phone)
        if currency is None or currency == self.base_currency:
            return base_bal
        return self.from_base(base_bal, currency)

    def get_transaction_history(self, phone: str) -> list:
        """Return the list of transactions for a given phone number."""
        self.find_account(phone)  # Ensure user exists
        return self.transactions.get(phone, [])

    def save_data(self, filename: str = "data.json") -> None:
        """Serialize balances, contacts, and liquidity to a JSON file."""
        data = {
            "balances": self.balances,
            "contacts": self.contacts,
            "liquidity": self.liquidity,
            "base_currency": self.base_currency,
            "transactions": self.transactions
        }
        with open(filename, 'w') as f:
            json.dump(data, f, indent=4)

    def load_data(self, filename: str = "data.json") -> None:
        """Restore state from a JSON file if it exists."""
        if not os.path.exists(filename):
            return

        try:
            with open(filename, 'r') as f:
                data = json.load(f)
            
            # Map back to internal storage
            self.balances = data.get("balances", {})
            self.contacts = data.get("contacts", {})
            self.liquidity = data.get("liquidity", self.liquidity)
            self.base_currency = data.get("base_currency", self.base_currency)
            self.transactions = data.get("transactions", {})
        except (json.JSONDecodeError, IOError) as e:
            print(f"Warning: Could not load persistence data: {e}")