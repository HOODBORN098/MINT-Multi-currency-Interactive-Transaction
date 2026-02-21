import unittest
import os
from banking.financial_engine import FinancialEngine

class TestHistory(unittest.TestCase):
    def setUp(self):
        self.filename = "test_history.json"
        if os.path.exists(self.filename):
            os.remove(self.filename)
        self.engine = FinancialEngine(base_currency="USD")
        self.phone = "1234567890"
        self.engine.register(self.phone, "Test User")

    def tearDown(self):
        if os.path.exists(self.filename):
            os.remove(self.filename)

    def test_history_logging(self):
        # 1. Perform transactions
        self.engine.deposit(self.phone, 100, "USD")
        self.engine.withdraw(self.phone, 20, "USD")
        self.engine.exchange(self.phone, "USD", "EUR", 10)
        
        # 2. Get history
        history = self.engine.get_transaction_history(self.phone)
        
        # 3. Verify
        self.assertEqual(len(history), 3)
        self.assertEqual(history[0]["action"], "Deposit")
        self.assertEqual(history[1]["action"], "Withdrawal")
        self.assertEqual(history[2]["action"], "Exchange")
        self.assertEqual(history[2]["currency"], "USD")
        self.assertIn("To EUR", history[2]["details"])

    def test_transfer_history(self):
        receiver = "0987654321"
        self.engine.register(receiver, "Receiver")
        self.engine.deposit(self.phone, 100, "USD")
        
        # Transfer
        self.engine.transfer(self.phone, receiver, 40, "USD")
        
        # Verify Sender
        sender_hist = self.engine.get_transaction_history(self.phone)
        self.assertEqual(sender_hist[-1]["action"], "Transfer Out")
        self.assertIn(receiver, sender_hist[-1]["details"])
        
        # Verify Receiver
        recv_hist = self.engine.get_transaction_history(receiver)
        self.assertEqual(recv_hist[-1]["action"], "Transfer In")
        self.assertIn(self.phone, recv_hist[-1]["details"])

    def test_history_persistence(self):
        self.engine.deposit(self.phone, 100, "USD")
        self.engine.save_data(self.filename)
        
        # Load in new engine
        new_engine = FinancialEngine()
        new_engine.load_data(self.filename)
        
        history = new_engine.get_transaction_history(self.phone)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["action"], "Deposit")

if __name__ == "__main__":
    unittest.main()
