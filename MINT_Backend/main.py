"""
Console interface for the banking system.
"""
from banking.financial_engine import FinancialEngine

def print_menu():
    print("\n===== BANKING SYSTEM =====")
    print("1. Register user")
    print("2. Deposit")
    print("3. Withdraw")
    print("4. Exchange")
    print("5. Transfer")
    print("6. Credit (admin)")
    print("7. Debit (admin)")
    print("8. View balance")
    print("9. View transaction history")
    print("10. Exit")
    print("==========================")

def main():
    # Choose base currency once (fix the bug)
    base = input("Enter base currency (USD/EUR/GBP/JPY) [USD]: ").upper() or "USD"
    if base not in ["USD", "EUR", "GBP", "JPY"]:
        print("Invalid base currency. Using USD.")
        base = "USD"
    bank = FinancialEngine(base_currency=base)
    bank.load_data()  # Load existing session if any

    while True:
        print_menu()
        choice = input("Choose an option: ").strip()

        try:
            if choice == "1":
                phone = input("Phone number: ").strip()
                name = input("Full name: ").strip()
                bank.register(phone, name)
                bank.save_data()
                print(f"User {name} registered successfully.")

            elif choice == "2":
                phone = input("Your phone: ").strip()
                amount = float(input("Amount: "))
                currency = input("Currency (USD/EUR/GBP/JPY): ").upper()
                bank.deposit(phone, amount, currency)
                bank.save_data()
                print("Deposit successful.")

            elif choice == "3":
                phone = input("Your phone: ").strip()
                amount = float(input("Amount: "))
                currency = input("Currency (USD/EUR/GBP/JPY): ").upper()
                bank.withdraw(phone, amount, currency)
                bank.save_data()
                print("Withdrawal successful.")

            elif choice == "4":
                phone = input("Your phone: ").strip()
                from_cur = input("From currency: ").upper()
                to_cur = input("To currency: ").upper()
                amount = float(input("Amount to exchange: "))
                bank.exchange(phone, from_cur, to_cur, amount)
                bank.save_data()
                print("Exchange successful.")

            elif choice == "5":
                sender = input("Your phone (sender): ").strip()
                receiver = input("Receiver's phone: ").strip()
                amount = float(input("Amount: "))
                currency = input("Currency (USD/EUR/GBP/JPY): ").upper()
                bank.transfer(sender, receiver, amount, currency)
                bank.save_data()
                print("Transfer successful.")

            elif choice == "6":
                phone = input("User phone: ").strip()
                amount = float(input("Amount to credit: "))
                currency = input("Currency: ").upper()
                bank.credit(phone, amount, currency)
                bank.save_data()
                print("Credit successful.")

            elif choice == "7":
                phone = input("User phone: ").strip()
                amount = float(input("Amount to debit: "))
                currency = input("Currency: ").upper()
                bank.debit(phone, amount, currency)
                bank.save_data()
                print("Debit successful.")

            elif choice == "8":
                phone = input("Your phone: ").strip()
                curr = input("View in currency (leave empty for base): ").upper() or None
                balance = bank.show_balance(phone, curr)
                if curr:
                    print(f"Your balance: {balance:.2f} {curr}")
                else:
                    print(f"Your balance (in {bank.base_currency}): {balance:.2f}")

            elif choice == "9":
                phone = input("Your phone: ").strip()
                history = bank.get_transaction_history(phone)
                if not history:
                    print("No transactions found.")
                else:
                    print(f"\n--- Transaction History for {phone} ---")
                    print(f"{'Timestamp':<20} | {'Action':<15} | {'Amount':<10} | {'Cur':<4} | {'Details'}")
                    print("-" * 80)
                    for t in history:
                        print(f"{t['timestamp']:<20} | {t['action']:<15} | {t['amount']:<10.2f} | {t['currency']:<4} | {t['details']}")

            elif choice == "10":
                bank.save_data()
                print("Goodbye!")
                break

            else:
                print("Invalid option. Please choose 1-10.")

        except ValueError as e:
            print(f"Error: {e}")
        except Exception as e:
            print(f"Unexpected error: {e}")

if __name__ == "__main__":
    main()
