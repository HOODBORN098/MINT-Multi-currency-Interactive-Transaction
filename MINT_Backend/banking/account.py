"""
Account – user registry, inherits from CurrencyWallet.
"""
from .currency_wallet import CurrencyWallet

class Account(CurrencyWallet):
    """
    Maintains a mapping from phone number to user name.
    """
    def __init__(self):
        super().__init__()
        self.contacts = {}  # phone -> name

    def register(self, phone: str, name: str) -> None:
        """Register a new user with given phone and name."""
        if phone in self.contacts:
            raise ValueError(f"User with phone {phone} already exists.")
        self.contacts[phone] = name

    def exists(self, phone: str) -> bool:
        """Check if a user with the given phone exists."""
        return phone in self.contacts

    def get_name(self, phone: str) -> str:
        """Return the name associated with a phone number."""
        if not self.exists(phone):
            raise ValueError(f"User {phone} not found.")
        return self.contacts[phone]
