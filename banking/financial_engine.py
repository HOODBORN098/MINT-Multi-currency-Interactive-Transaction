"""
FinancialEngine – core banking operations, inherits from FindAccount.
"""
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

    def _get_base_balance(self, phone: str) -> float:
        """Internal helper to get current balance (base currency)."""
        return self.balances.get(phone, 0.0)

    def deposit(self, phone: str, amount: float, currency: str) -> None:
        """User deposits cash; bank receives currency and increases user's balance."""
        # Convert to base and update user balance
        base_amount = self.to_base(amount, currency)
        self.balances[phone] = self._get_base_balance(phone) + base_amount
        # Bank receives the currency
        self.adjust_liquidity(currency, amount)

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

    def show_balance(self, phone: str, currency: str | None = None) -> float:
        """
        Return the user's balance. If currency is None or matches base_currency,
        return the base amount; otherwise convert to the requested currency.
        """
        base_bal = self._get_base_balance(phone)
        if currency is None or currency == self.base_currency:
            return base_bal
        return self.from_base(base_bal, currency)