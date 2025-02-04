from fastapi import APIRouter
from app.models.models import UserResponse, UserCreate, BankAccountResponse
import mysql.connector
from fastapi import HTTPException, Depends, status
from typing import Annotated, List
from mysql.connector import Error as MySQLError
from app.db import get_db
from app.logger import logger
from app.routes.user.service import is_email_taken, is_phone_taken, create_bank_account, hash_password
from app.services.auth_service import get_current_user

router = APIRouter(
    prefix="/user",
    tags=["User"]
)

@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register_user(
        user: UserCreate,
        db: Annotated[mysql.connector.MySQLConnection, Depends(get_db)]
):
    if is_email_taken(db, user.email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )

    if is_phone_taken(db, user.phone):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Phone number already registered"
        )

    cursor = None
    try:
        hashed_password = hash_password(user.password)
        cursor = db.cursor(dictionary=True)

        # Starting db transaction
        cursor.execute("START TRANSACTION")

        # Inserting user
        cursor.execute("""
            INSERT INTO users (email, phone, password_hash, full_name)
            VALUES (%s, %s, %s, %s)
        """, (user.email, user.phone, hashed_password, user.full_name))

        user_id = cursor.lastrowid

        # Creating a default bank account/wallet
        create_bank_account(cursor, user_id, user.bank_name)

        # Commiting the transaction
        cursor.execute("COMMIT")

        # Fetching created user
        cursor.execute("""
            SELECT id, email, phone, full_name, created_at
            FROM users WHERE id = %s
        """, (user_id,))
        new_user = cursor.fetchone()

        return UserResponse(**new_user)

    except MySQLError as e:
        if cursor:
            cursor.execute("ROLLBACK")
        logger.error(f"Database error in register_user: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error: {str(e)}"
        )
    finally:
        if cursor:
            cursor.close()

@router.get("/account/", response_model=List[BankAccountResponse])
async def list_bank_accounts(
        current_user: dict = Depends(get_current_user),
        db: mysql.connector.MySQLConnection = Depends(get_db)
):
    cursor = None
    try:
        cursor = db.cursor(dictionary=True)
        cursor.execute("""
            SELECT * FROM bank_accounts 
            WHERE user_id = %s
            ORDER BY created_at DESC
        """, (current_user["id"],))

        accounts = cursor.fetchall()
        return accounts
    finally:
        if cursor:
            cursor.close()
