import os
from decimal import Decimal
from fastapi.security import OAuth2PasswordBearer

# OAuth2 scheme
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")

# Security configurations
SECRET_KEY = os.getenv("JWT_SECRET_KEY")
if not SECRET_KEY:
    raise ValueError("JWT_SECRET_KEY environment variable is not set")

def get_bank_account(cursor, bank_account_id: int, user_id: int):
    cursor.execute(
        "SELECT * FROM bank_accounts WHERE id = %s AND user_id = %s",
        (bank_account_id, user_id)
    )
    return cursor.fetchone()

def update_bank_balance(cursor, bank_account_id: int, amount: Decimal):
    cursor.execute(
        """
        UPDATE bank_accounts 
        SET current_balance = current_balance + %s 
        WHERE id = %s
        """,
        (amount, bank_account_id)
    )

