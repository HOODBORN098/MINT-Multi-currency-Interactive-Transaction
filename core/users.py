#!/usr/bin/env python3
"""
View ChainPay Registered Users
===============================
Simple utility to view all registered users and their wallet balances.
"""

import sqlite3
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chainpay.db")

def view_users():
    if not os.path.exists(DB_PATH):
        print("❌ Database not found. Run 'python main.py' first to create it.")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    try:
        # Get all users
        users = conn.execute("""
            SELECT user_id, phone, name, kyc_status, role, created_at, last_login 
            FROM users 
            ORDER BY created_at DESC
        """).fetchall()

        if not users:
            print("📭 No users registered yet.")
            return

        print(f"\n{'='*100}")
        print(f"  ⬡ CHAINPAY REGISTERED USERS: {len(users)}")
        print(f"{'='*100}")
        
        for i, user in enumerate(users, 1):
            created = datetime.fromtimestamp(user['created_at']).strftime('%Y-%m-%d %H:%M') if user['created_at'] else 'N/A'
            last_login = datetime.fromtimestamp(user['last_login']).strftime('%Y-%m-%d %H:%M') if user['last_login'] else 'Never'
            
            print(f"\n  #{i}  {user['name']}")
            print(f"  {'─'*96}")
            print(f"  📱 Phone:     {user['phone']}")
            print(f"  🆔 User ID:   {user['user_id']}")
            print(f"  🛡️  KYC:       {user['kyc_status']} | Role: {user['role']}")
            print(f"  📅 Created:   {created} | Last Login: {last_login}")
            
            # Get wallets for this user
            wallets = conn.execute("""
                SELECT currency, balance FROM wallets 
                WHERE user_id = ? ORDER BY currency
            """, (user['user_id'],)).fetchall()
            
            if wallets:
                print(f"  💰 Wallets:")
                for w in wallets:
                    balance = w['balance'] / 100  # Convert cents to main units
                    symbol = {"$": "USD", "€": "EUR", "£": "GBP", "₦": "NGN", "KES ": "KES"}.get(
                        {"USD": "$", "EUR": "€", "GBP": "£", "NGN": "₦", "KES": "KES "}.get(w['currency'], w['currency']), 
                        w['currency']
                    )
                    print(f"      {w['currency']}: {balance:,.2f} {symbol}")

        print(f"\n{'='*100}\n")

    except sqlite3.Error as e:
        print(f"❌ Database error: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    view_users()