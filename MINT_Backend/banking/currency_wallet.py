"""
CurrencyWallet – base class for exchange rates and liquidity.
"""

class CurrencyWallet:
    """
    Manages exchange rates (relative to a base currency) and the institution's
    liquidity pool for each currency.
    """
    def __init__(self):
        # Exchange rates relative to USD (base)
        self.rates = {
            "USD": 1.0,
            "EUR": 0.85,
            "GBP": 0.73,
            "JPY": 110.0
        }
        # Initial liquidity (bank's own holdings)
        self.liquidity = {
            "USD": 10000,
            "EUR": 10000,
            "GBP": 10000,
            "JPY": 1000000
        }

    def to_base(self, amount: float, currency: str) -> float:
        """Convert an amount in given currency to base currency (USD)."""
        if currency not in self.rates:
            raise ValueError(f"Unsupported currency: {currency}")
        return amount / self.rates[currency]

    def from_base(self, amount: float, currency: str) -> float:
        """Convert an amount from base currency to given currency."""
        if currency not in self.rates:
            raise ValueError(f"Unsupported currency: {currency}")
        return amount * self.rates[currency]

    def convert(self, amount: float, from_cur: str, to_cur: str) -> float:
        """Convert amount from one currency to another via base currency."""
        base_amount = self.to_base(amount, from_cur)
        return self.from_base(base_amount, to_cur)

    def check_liquidity(self, currency: str, amount: float) -> bool:
        """Return True if the bank has at least `amount` of `currency`."""
        return self.liquidity.get(currency, 0) >= amount

    def adjust_liquidity(self, currency: str, amount: float) -> None:
        """
        Increase (positive amount) or decrease (negative amount) the bank's
        liquidity for the given currency.
        """
        if currency not in self.liquidity:
            raise ValueError(f"Unsupported currency: {currency}")
        self.liquidity[currency] += amount
        # Liquidity should never become negative – but we check before calling.
        if self.liquidity[currency] < 0:
            # This indicates a programming error; raise a more specific exception.
            raise RuntimeError(f"Liquidity for {currency} became negative!")
