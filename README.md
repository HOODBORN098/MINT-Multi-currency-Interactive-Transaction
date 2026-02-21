# Banking System (Multi‑Currency Wallet)

A console‑based Python application that simulates a simple multi‑currency banking system with exchange, transfers, and admin operations. Built as a team project to demonstrate OOP inheritance and basic financial logic.

## Features
- Four currencies: USD, EUR, GBP, JPY.
- Users identified by phone number.
- Deposit, withdraw, exchange, transfer, credit, debit, balance inquiry.
- Bank holds liquidity in each currency.
- All user balances stored internally in a chosen base currency.
- Small overdraft allowed.

## Project Structure
- `main.py` – entry point, interactive menu.
- `banking/` – package containing the four classes in an inheritance chain:
  - `currency_wallet.py` – exchange rates & liquidity.
  - `account.py` – user registry.
  - `find_account.py` – lookup helper.
  - `financial_engine.py` – core banking operations.
- `tests/` – unit tests for each class.

## Team分工 (4 Members)
- **Member 1** – `CurrencyWallet` (base layer)
- **Member 2** – `Account` & `FindAccount` (user management)
- **Member 3** – `FinancialEngine` (core logic)
- **Member 4** – Console interface (`main.py`) and integration

## How to Run
1. Clone the repository.
2. Ensure you have Python 3.6+ installed.
3. Run `python main.py` from the project root.

## Future Enhancements
- Persistence (JSON or database)
- Transaction history
- Authentication
- Dynamic exchange rates
- Decimal type for precision
