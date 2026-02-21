"""
FindAccount – adds a find_account convenience method.
"""
from .account import Account

class FindAccount(Account):
    """
    Adds a method to safely retrieve a phone number if the user exists.
    """
    def find_account(self, phone: str) -> str:
        """
        Return the phone number if the user exists; otherwise raise ValueError.
        """
        if not self.exists(phone):
            raise ValueError(f"User {phone} not found.")
        return phone