import unittest
import os
import json
from banking.financial_engine import FinancialEngine

class TestPersistence(unittest.TestCase):
    def setUp(self):
        self.filename = "test_data.json"
        if os.path.exists(self.filename):
            os.remove(self.filename)
        self.engine = FinancialEngine(base_currency="USD")

    def tearDown(self):
        if os.path.exists(self.filename):
            os.remove(self.filename)

    def test_save_and_load(self):
        # 1. Setup sample data
        phone = "1234567890"
        name = "Test User"
        self.engine.register(phone, name)
        self.engine.deposit(phone, 1000, "USD")
        self.engine.adjust_liquidity("EUR", 500)
        
        # 2. Save
        self.engine.save_data(self.filename)
        self.assertTrue(os.path.exists(self.filename))
        
        # 3. Load into a new engine
        new_engine = FinancialEngine(base_currency="GBP") # different default
        new_engine.load_data(self.filename)
        
        # 4. Verify
        self.assertEqual(new_engine.get_name(phone), name)
        self.assertEqual(new_engine.show_balance(phone), 1000)
        self.assertEqual(new_engine.base_currency, "USD")
        self.assertEqual(new_engine.liquidity["EUR"], 10500) # Initial 10000 + 500

if __name__ == "__main__":
    unittest.main()
