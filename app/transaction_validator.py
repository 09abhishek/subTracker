from decimal import Decimal
from typing import List, Annotated, Optional, Dict
import mysql.connector


class TransactionValidator:
    """Handles transaction validation logic"""

    def __init__(self, db: mysql.connector.MySQLConnection, user_id: int):
        self.db = db
        self.user_id = user_id
        self.cursor = db.cursor(dictionary=True)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cursor.close()

    def get_user_balance(self, user_email: str) -> Dict:
        """Get user's bank account info and current balance"""
        self.cursor.execute("""
            SELECT 
                u.id as user_id,
                b.id as bank_account_id,
                b.current_balance,
                b.account_name
            FROM users u
            JOIN bank_accounts b ON u.id = b.user_id
            WHERE u.email = %s
            ORDER BY b.created_at ASC
            LIMIT 1
        """, (user_email,))
        return self.cursor.fetchone()

    def check_existing_transactions(self, date: str, amount: Decimal) -> bool:
        """Check if transaction already exists in database"""
        self.cursor.execute("""
            SELECT EXISTS(
                SELECT 1 FROM transactions
                WHERE user_id = %s 
                AND date = %s 
                AND ABS(amount) = %s
            ) as exists_in_db
        """, (self.user_id, date, str(abs(amount))))
        result = self.cursor.fetchone()
        return result['exists_in_db'] if result else False

    def find_repeated_transactions(self, transactions: List[Dict]) -> tuple:
        """Find repeated transactions within the file"""
        transaction_map = {}
        repeated = []
        unique = []

        for tx in transactions:
            # Create unique key for transaction
            key = f"{tx['date']}_{tx['amount']}_{tx['description']}"

            if key in transaction_map:
                repeated.append({
                    **tx,
                    "validation_message": "Duplicate entry found in uploaded file",
                    "duplicate_of": transaction_map[key]['index']
                })
            else:
                transaction_map[key] = {
                    "transaction": tx,
                    "index": len(unique)
                }
                unique.append(tx)

        return repeated, unique