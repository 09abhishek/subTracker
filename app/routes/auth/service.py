import mysql.connector
from fastapi import HTTPException, status
from mysql.connector import Error as MySQLError
from app.config import PWD_CONTEXT
from app.logger import logger
from decimal import Decimal

def is_email_taken(db, email: str) -> bool:
    try:
        cursor = db.cursor()
        cursor.execute("SELECT id FROM users WHERE email = %s", (email,))
        result = cursor.fetchone()
        return result is not None
    finally:
        cursor.close()


def is_phone_taken(db, phone: str) -> bool:
    try:
        cursor = db.cursor()
        cursor.execute("SELECT id FROM users WHERE phone = %s", (phone,))
        result = cursor.fetchone()
        return result is not None
    finally:
        cursor.close()

def hash_password(password: str) -> str:
    try:
        return PWD_CONTEXT.hash(password)
    except Exception as e:
        logger.error(f"Password hashing error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error processing password"
        )


def create_bank_account(cursor, user_id: int, bank_name: str) -> int:
    """Creates a default bank account for a users and returns the account ID"""
    try:
        cursor.execute(
            """
            INSERT INTO bank_accounts 
                (user_id, account_name, current_balance)
            VALUES 
                (%s, %s, %s)
            """,
            (user_id, bank_name, Decimal('0.00'))
        )
        return cursor.lastrowid
    except MySQLError as e:
        logger.error(f"Database error in create_bank_account: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error creating default bank account"
        )