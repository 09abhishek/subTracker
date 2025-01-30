from decimal import Decimal

from fastapi import FastAPI, UploadFile, File, HTTPException, Depends, status, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordRequestForm
from jose import JWTError, jwt
from datetime import datetime, timedelta
from typing import List, Annotated, Optional
import mysql.connector
from mysql.connector import Error as MySQLError
from app.config import REFRESH_TOKEN_EXPIRE_DAYS, ACCESS_TOKEN_EXPIRE_MINUTES
from app.db import get_db
from app.helper import is_email_taken, is_phone_taken, hash_password, create_bank_account, get_current_user, \
    get_bank_account, get_category_by_id, update_bank_balance, oauth2_scheme, SECRET_KEY, ALGORITHM, authenticate_user, \
    store_tokens, create_access_token
from app.ledger_parser import parse_ledger_entries, process_transactions
from app.logger import logger
from app.models import BalanceResponse, ExpenseCreate, BalanceUpdate, TransactionType, UserResponse, \
    UserCreate, TransactionResponse, TransactionCreate, BankAccountResponse, CategoryResponse, CategoryCreate, Token
from app.transaction_validator import TransactionValidator

# FastAPI app instance
app = FastAPI(
    title="SubTracker API",
    description="API for processing and managing personal finance ledger files",
    version="1.0.0"
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
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

        # Start transaction
        cursor.execute("START TRANSACTION")

        # Insert user
        cursor.execute("""
            INSERT INTO users (email, phone, password_hash, full_name)
            VALUES (%s, %s, %s, %s)
        """, (user.email, user.phone, hashed_password, user.full_name))

        user_id = cursor.lastrowid

        # Create default bank account
        create_bank_account(cursor, user_id, user.bank_name)

        # Commit transaction
        cursor.execute("COMMIT")

        # Fetch created user
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


@app.post("/transactions/", response_model=TransactionResponse)
async def create_transaction(
        transaction: TransactionCreate,
        current_user: dict = Depends(get_current_user),
        db: mysql.connector.MySQLConnection = Depends(get_db)
):
    cursor = None
    try:
        cursor = db.cursor(dictionary=True)

        # Verify bank account belongs to user
        bank_account = get_bank_account(cursor, transaction.bank_account_id, current_user["id"])
        if not bank_account:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Bank account not found"
            )

        # Verify category exists
        category = get_category_by_id(cursor, transaction.category_id)
        if not category:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Category not found"
            )

        # Start transaction
        cursor.execute("START TRANSACTION")

        # Create transaction
        cursor.execute("""
            INSERT INTO transactions 
                (user_id, bank_account_id, category_id, date, description, amount, source)
            VALUES 
                (%s, %s, %s, %s, %s, %s, %s)
        """, (
            current_user["id"],
            transaction.bank_account_id,
            transaction.category_id,
            transaction.date,
            transaction.description,
            transaction.amount,
            transaction.source
        ))

        transaction_id = cursor.lastrowid

        # Update bank account balance
        update_bank_balance(cursor, transaction.bank_account_id, transaction.amount)

        # Commit transaction
        cursor.execute("COMMIT")

        # Fetch created transaction
        cursor.execute("""
            SELECT * FROM transactions WHERE id = %s
        """, (transaction_id,))
        new_transaction = cursor.fetchone()

        return TransactionResponse(**new_transaction)

    except MySQLError as e:
        if cursor:
            cursor.execute("ROLLBACK")
        logger.error(f"Database error in create_transaction: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error: {str(e)}"
        )
    finally:
        if cursor:
            cursor.close()


@app.get("/transactions/", response_model=List[TransactionResponse])
async def list_transactions(
        current_user: dict = Depends(get_current_user),
        db: mysql.connector.MySQLConnection = Depends(get_db),
        skip: int = 0,
        limit: int = 100
):
    try:
        cursor = db.cursor(dictionary=True)
        cursor.execute("""
            SELECT * FROM transactions 
            WHERE user_id = %s
            ORDER BY date DESC, id DESC
            LIMIT %s OFFSET %s
        """, (current_user["id"], limit, skip))

        transactions = cursor.fetchall()
        return transactions
    finally:
        if cursor:
            cursor.close()


@app.get("/categories/", response_model=List[CategoryResponse])
async def list_categories(
        db: mysql.connector.MySQLConnection = Depends(get_db),
        type: Optional[TransactionType] = None
):
    cursor = None
    try:
        cursor = db.cursor(dictionary=True)
        if type:
            cursor.execute(
                "SELECT * FROM categories WHERE type = %s ORDER BY name",
                (type,)
            )
        else:
            cursor.execute("SELECT * FROM categories ORDER BY name")

        categories = cursor.fetchall()
        return categories
    finally:
        if cursor:
            cursor.close()


@app.get("/bank-accounts/", response_model=List[BankAccountResponse])
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


"""
    Get the current balance for the authenticated user's primary bank account.
    Requires a valid JWT token in the Authorization header.
    """


@app.get("/balance", response_model=BalanceResponse)
async def get_account_balance(
        current_user: dict = Depends(get_current_user),
        token: str = Depends(oauth2_scheme),
        db: mysql.connector.MySQLConnection = Depends(get_db)
):
    """
    Get the current balance and user details for the authenticated user.
    Requires a valid JWT token in the Authorization header.
    """
    if not current_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    cursor = None
    try:
        cursor = db.cursor(dictionary=True)
        cursor.execute("""
            SELECT 
                u.email,
                u.phone,
                u.full_name,
                b.account_name,
                b.current_balance
            FROM users u
            JOIN bank_accounts b ON u.id = b.user_id
            WHERE u.id = %s
            ORDER BY b.created_at ASC
            LIMIT 1
        """, (current_user["id"],))

        result = cursor.fetchone()
        if not result:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No bank account found for user"
            )

        return result

    except MySQLError as e:
        logger.error(f"Database error in get_account_balance: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error retrieving account balance"
        )
    finally:
        if cursor:
            cursor.close()


@app.post("/transactions/expense", response_model=TransactionResponse, status_code=status.HTTP_201_CREATED)
async def create_expense_transaction(
        expense: ExpenseCreate,
        current_user: dict = Depends(get_current_user),
        db: mysql.connector.MySQLConnection = Depends(get_db)
):
    """
    Create an expense transaction with proper balance validation.
    The amount will be deducted from the user's bank account balance.
    """
    cursor = None
    try:
        cursor = db.cursor(dictionary=True)

        # Start transaction
        cursor.execute("START TRANSACTION")

        # 1. Get user's bank account and verify sufficient balance
        cursor.execute("""
            SELECT id, current_balance 
            FROM bank_accounts 
            WHERE user_id = %s 
            ORDER BY created_at ASC 
            LIMIT 1
            FOR UPDATE  -- Lock the row for updating
        """, (current_user["id"],))

        bank_account = cursor.fetchone()
        if not bank_account:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No bank account found for user"
            )

        # 2. Verify category exists and is an expense category
        cursor.execute("""
            SELECT id, type 
            FROM categories 
            WHERE id = %s AND type = 'expense'
        """, (expense.category_id,))

        category = cursor.fetchone()
        if not category:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid expense category"
            )

        # 3. Check if sufficient balance available
        expense_amount = abs(expense.amount) * -1  # Convert to negative for expense
        new_balance = bank_account['current_balance'] + expense_amount

        if new_balance < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "message": "Insufficient balance",
                    "current_balance": str(bank_account['current_balance']),
                    "expense_amount": str(abs(expense_amount)),
                    "required_additional": str(abs(new_balance))
                }
            )

        # 4. Create the transaction record
        cursor.execute("""
            INSERT INTO transactions 
                (user_id, bank_account_id, category_id, date, description, amount, source)
            VALUES 
                (%s, %s, %s, %s, %s, %s, 'manual')
        """, (
            current_user["id"],
            bank_account["id"],
            expense.category_id,
            expense.transaction_date,
            expense.description,
            expense_amount,  # Store as negative amount
        ))

        transaction_id = cursor.lastrowid

        # 5. Update bank account balance
        cursor.execute("""
            UPDATE bank_accounts 
            SET current_balance = current_balance + %s,
                updated_at = CURRENT_TIMESTAMP 
            WHERE id = %s
        """, (expense_amount, bank_account["id"]))

        # 6. Commit the transaction
        cursor.execute("COMMIT")

        # 7. Fetch and return the created transaction
        cursor.execute("""
            SELECT t.*, c.type as category_type, c.name as category_name,
                   ba.current_balance as updated_balance
            FROM transactions t
            JOIN categories c ON t.category_id = c.id
            JOIN bank_accounts ba ON t.bank_account_id = ba.id
            WHERE t.id = %s
        """, (transaction_id,))

        transaction = cursor.fetchone()
        return transaction

    except MySQLError as e:
        if cursor:
            cursor.execute("ROLLBACK")
        logger.error(f"Database error in create_expense_transaction: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error: {str(e)}"
        )
    finally:
        if cursor:
            cursor.close()


@app.post("/categories/setup", response_model=List[CategoryResponse])
async def setup_default_categories(
        db: mysql.connector.MySQLConnection = Depends(get_db)
):
    """Setup default categories if they don't exist. Preserves existing categories."""
    default_categories = [
        (1, "Salary", "income", "Regular employment income"),
        (2, "Investment Returns", "income", "Returns from mutual funds and investments"),
        (3, "Freelance", "income", "Freelance and project-based income"),
        (4, "Other Income", "income", "Miscellaneous income"),
        (5, "Food & Dining", "expense", "Groceries and restaurants"),
        (6, "Utilities", "expense", "Electricity, internet, and bills"),
        (7, "Transportation", "expense", "Fuel and travel expenses"),
        (8, "Health", "expense", "Medical and pharmacy expenses"),
        (9, "Shopping", "expense", "Online and offline shopping"),
        (10, "EMI & Payments", "expense", "Loan EMIs and credit card payments"),
        (11, "Investment", "expense", "Mutual funds and investments"),
        (12, "Internal Transfer", "transfer", "Account transfers")
    ]

    cursor = None
    try:
        cursor = db.cursor(dictionary=True)

        # Start transaction
        cursor.execute("START TRANSACTION")

        # First, get existing categories
        cursor.execute("SELECT id, name, type FROM categories")
        existing_categories = cursor.fetchall()
        existing_names = {(cat['name'], cat['type']) for cat in existing_categories}

        # Add only new categories
        for id_, name, type_, description in default_categories:
            if (name, type_) not in existing_names:
                cursor.execute("""
                    INSERT INTO categories (name, type, description)
                    VALUES (%s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        type = VALUES(type),
                        description = VALUES(description)
                """, (name, type_, description))
                logger.info(f"Added new category: {name} ({type_})")

        # Commit transaction
        cursor.execute("COMMIT")

        # Fetch all categories
        cursor.execute("""
            SELECT * FROM categories 
            ORDER BY type, name
        """)

        categories = cursor.fetchall()

        # Log category count by type
        type_counts = {}
        for cat in categories:
            type_counts[cat['type']] = type_counts.get(cat['type'], 0) + 1
        logger.info(f"Category counts by type: {type_counts}")

        return categories

    except MySQLError as e:
        if cursor:
            cursor.execute("ROLLBACK")
        logger.error(f"Database error in setup_default_categories: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error: {str(e)}"
        )
    finally:
        if cursor:
            cursor.close()


@app.post("/categories", response_model=CategoryResponse)
async def create_category(
        category: CategoryCreate,
        db: mysql.connector.MySQLConnection = Depends(get_db)
):
    """Create a new custom category if it doesn't exist"""
    cursor = None
    try:
        cursor = db.cursor(dictionary=True)

        # Check if category already exists case-insensitive search
        cursor.execute("""
            SELECT * FROM categories 
            WHERE LOWER(name) = LOWER(%s) AND type = %s
        """, (category.name.strip(), category.type))

        existing_category = cursor.fetchone()
        if existing_category:
            return existing_category  # Return existing category instead of creating new one

        # Create new category only if it doesn't exist
        cursor.execute("""
            INSERT INTO categories (name, type, description)
            VALUES (%s, %s, %s)
        """, (category.name.strip(), category.type, category.description))

        category_id = cursor.lastrowid
        db.commit()  # Commit the transaction

        # Fetch and return created category
        cursor.execute("SELECT * FROM categories WHERE id = %s", (category_id,))
        new_category = cursor.fetchone()

        return new_category

    except MySQLError as e:
        logger.error(f"Database error in create_category: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error: {str(e)}"
        )
    finally:
        if cursor:
            cursor.close()


@app.get("/categories/by-type/{type}", response_model=List[CategoryResponse])
async def get_categories_by_type(
        type: TransactionType,
        db: mysql.connector.MySQLConnection = Depends(get_db)
):
    """Get all categories of a specific type"""
    cursor = None
    try:
        cursor = db.cursor(dictionary=True)
        cursor.execute("""
            SELECT * FROM categories 
            WHERE type = %s 
            ORDER BY name
        """, (type,))

        categories = cursor.fetchall()
        return categories

    except MySQLError as e:
        logger.error(f"Database error in get_categories_by_type: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error: {str(e)}"
        )
    finally:
        if cursor:
            cursor.close()


@app.put("/accounts/balance", response_model=BankAccountResponse)
async def update_account_balance(
        balance_update: BalanceUpdate,
        current_user: dict = Depends(get_current_user),
        db: mysql.connector.MySQLConnection = Depends(get_db)
):
    """
    Update account balance with transaction tracking.
    Handles both deposits (positive amount) and withdrawals (negative amount).
    Ensures sufficient balance for withdrawals.
    """
    cursor = None
    try:
        cursor = db.cursor(dictionary=True)

        # Start transaction
        cursor.execute("START TRANSACTION")

        # 1. Verify and lock bank account
        cursor.execute("""
            SELECT * FROM bank_accounts 
            WHERE id = %s AND user_id = %s 
            FOR UPDATE
        """, (balance_update.bank_account_id, current_user["id"]))

        account = cursor.fetchone()
        if not account:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Bank account not found or unauthorized access"
            )

        # 2. Verify category exists and matches transaction type
        cursor.execute("""
            SELECT * FROM categories 
            WHERE id = %s AND type = %s
        """, (balance_update.category_id, balance_update.transaction_type))

        category = cursor.fetchone()
        if not category:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid category for {balance_update.transaction_type} transaction"
            )

        # 3. Check balance for withdrawals
        new_balance = account['current_balance'] + balance_update.amount
        if new_balance < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "message": "Insufficient balance for this transaction",
                    "current_balance": str(account['current_balance']),
                    "requested_debit": str(abs(balance_update.amount)),
                    "deficit": str(abs(new_balance))
                }
            )

        # 4. Record the transaction
        cursor.execute("""
            INSERT INTO transactions (
                user_id, 
                bank_account_id, 
                category_id, 
                date, 
                description, 
                amount, 
                source
            ) VALUES (%s, %s, %s, CURDATE(), %s, %s, 'manual')
        """, (
            current_user["id"],
            balance_update.bank_account_id,
            balance_update.category_id,
            balance_update.description,
            balance_update.amount
        ))

        # 5. Update account balance
        cursor.execute("""
            UPDATE bank_accounts 
            SET current_balance = current_balance + %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s AND user_id = %s
        """, (
            balance_update.amount,
            balance_update.bank_account_id,
            current_user["id"]
        ))

        # 6. Commit the transaction
        cursor.execute("COMMIT")

        # 7. Fetch and return updated account details
        cursor.execute("""
            SELECT 
                ba.*,
                t.amount as last_transaction_amount,
                t.description as last_transaction_description,
                c.name as last_transaction_category
            FROM bank_accounts ba
            LEFT JOIN transactions t ON t.bank_account_id = ba.id 
            LEFT JOIN categories c ON t.category_id = c.id
            WHERE ba.id = %s AND ba.user_id = %s
            ORDER BY t.created_at DESC
            LIMIT 1
        """, (balance_update.bank_account_id, current_user["id"]))

        updated_account = cursor.fetchone()
        return updated_account

    except MySQLError as e:
        if cursor:
            cursor.execute("ROLLBACK")
        logger.error(f"Database error in update_account_balance: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error: {str(e)}"
        )
    finally:
        if cursor:
            cursor.close()


@app.post("/login", response_model=Token)
async def login(
        response: Response,
        form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
        db: Annotated[mysql.connector.MySQLConnection, Depends(get_db)]
):
    user = authenticate_user(db, form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        # Invalidate any existing tokens for this user
        cursor = db.cursor()
        cursor.execute(
            "DELETE FROM auth_tokens WHERE user_id = %s",
            (user["id"],)
        )
        db.commit()
        cursor.close()

        # Create new tokens
        access_token = create_access_token(
            data={"sub": user["email"]},
            expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        )
        refresh_token = create_access_token(
            data={"sub": str(user["id"])},
            expires_delta=timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
        )

        # Store both tokens in the database
        expires_at = datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
        store_tokens(db, user["id"], access_token, refresh_token, expires_at)

        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer"
        }
    except Exception as e:
        logger.error(f"Login error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error creating login tokens"
        )


@app.post("/refresh-token", response_model=Token)
async def refresh_token(
        refresh_token: str,
        db: mysql.connector.MySQLConnection = Depends(get_db)
):
    try:
        # Decode refresh token
        payload = jwt.decode(refresh_token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid refresh token"
            )

        # Verify token exists and belongs to user
        cursor = db.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT u.* FROM users u
            JOIN auth_tokens t ON u.id = t.user_id
            WHERE t.refresh_token = %s 
            AND t.expires_at > NOW()
            """,
            (refresh_token,)
        )
        user = cursor.fetchone()
        cursor.close()

        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired refresh token"
            )

        # Create new tokens
        access_token = create_access_token(
            data={"sub": user["email"]},
            expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        )
        new_refresh_token = create_access_token(
            data={"sub": str(user["id"])},
            expires_delta=timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
        )

        # Update tokens in database
        cursor = db.cursor()
        expires_at = datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)

        # Remove old tokens
        cursor.execute(
            "DELETE FROM auth_tokens WHERE refresh_token = %s",
            (refresh_token,)
        )

        # Store new tokens
        store_tokens(db, user["id"], access_token, new_refresh_token, expires_at)

        db.commit()
        cursor.close()

        return {
            "access_token": access_token,
            "refresh_token": new_refresh_token,
            "token_type": "bearer"
        }

    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate refresh token"
        )


@app.post("/logout")
async def logout(
        token: Annotated[str, Depends(oauth2_scheme)],
        current_user: dict = Depends(get_current_user),
        db: mysql.connector.MySQLConnection = Depends(get_db)
):
    try:
        cursor = db.cursor()
        # Delete all tokens for the user
        cursor.execute(
            "DELETE FROM auth_tokens WHERE user_id = %s",
            (current_user["id"],)
        )
        db.commit()

        return {"detail": "Successfully logged out"}

    except MySQLError as e:
        logger.error(f"Logout error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error during logout"
        )
    finally:
        if cursor:
            cursor.close()


@app.post("/upload-ledger")
async def upload_ledger_file(
        file: UploadFile = File(...),
        current_user: dict = Depends(get_current_user),
        db: mysql.connector.MySQLConnection = Depends(get_db)
):
    """
    Upload a .ledger file and process its transactions.
    Returns statistics about successful and failed transactions.
    """
    logger.info(f"Processing file: {file.filename}")

    # Validate file extension
    if not file.filename.endswith('.ledger'):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file format. Please upload a .ledger file"
        )
    cursor = None
    try:
        # Read and parse file
        content = await file.read()
        ledger_text = content.decode('utf-8')
        cursor = db.cursor(dictionary=True, buffered=True)
        parsed_transactions = parse_ledger_entries(ledger_text, cursor)

        # Process transactions
        results = await process_transactions(
            transactions=parsed_transactions,
            user_email=current_user['email'],
            db=db
        )

        # Prepare response message based on results
        message = "File processing completed."
        if results['total_success'] > 0 and results['total_failed'] > 0:
            message = f"Partially successful. {results['total_success']} transactions processed, {results['total_failed']} failed."
        elif results['total_success'] > 0:
            message = f"All {results['total_success']} transactions processed successfully."
        else:
            message = f"Processing failed. All {results['total_failed']} transactions failed."

        return {
            "message": message,
            "statistics": {
                "total_transactions": results['total_processed'],
                "successful_transactions": results['total_success'],
                "failed_transactions": results['total_failed']
            },
            "successful": results['successful'],
            "failed": results['failed']
        }

    except UnicodeDecodeError:
        logger.error("File decoding error")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File encoding error. Please ensure the file is UTF-8 encoded"
        )
    except Exception as e:
        logger.error(f"Error processing file: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error processing file: {str(e)}"
        )


@app.post("/verify-file")
async def verify_ledger_file(
        file: UploadFile = File(...),
        current_user: dict = Depends(get_current_user),
        db: mysql.connector.MySQLConnection = Depends(get_db)
):
    """
    Verify a ledger file before processing:
    1. Check for repeated transactions within the file
    2. Validate transaction feasibility based on account balance
    3. Check for existing transactions in database

    Returns categorized transactions and validation messages.
    """
    cursor = None
    try:
        # Validate file extension
        if not file.filename.endswith('.ledger'):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid file format. Please upload a .ledger file"
            )

        content = await file.read()
        ledger_text = content.decode('utf-8')

        cursor = db.cursor(dictionary=True, buffered=True)
        parsed_transactions = parse_ledger_entries(ledger_text, cursor)

        return parsed_transactions
        # Initialize validator
        with TransactionValidator(db, current_user['id']) as validator:
            # Get user's bank account info
            user_info = validator.get_user_balance(current_user['email'])
            if not user_info:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="User or bank account not found"
                )

            # Find repeated transactions
            repeated_transactions, unique_transactions = validator.find_repeated_transactions(parsed_transactions)

            # Initialize result containers
            current_balance = Decimal(str(user_info['current_balance']))
            existing_in_db = []
            processable_transactions = []
            unprocessable_transactions = []

            # Validate each unique transaction
            for tx in unique_transactions:
                # Convert amount to Decimal
                tx_amount = Decimal(str(tx['amount']))

                # Check if transaction exists in DB
                if validator.check_existing_transactions(tx['date'], tx_amount):
                    existing_in_db.append({
                        **tx,
                        "validation_message": "Transaction already exists in database"
                    })
                    continue

                # Calculate balance impact
                amount = tx_amount if tx['type'] == 'income' else -tx_amount
                new_balance = current_balance + amount

                # Validate based on transaction type and balance
                if tx['type'] == 'expense' and new_balance < 0:
                    unprocessable_transactions.append({
                        **tx,
                        "validation_message": (
                            f"Insufficient balance for expense. "
                            f"Required: {abs(amount)}, "
                            f"Available: {current_balance}"
                        )
                    })
                else:
                    processable_transactions.append({
                        **tx,
                        "validation_message": "Transaction is valid and can be processed",
                        "projected_balance": new_balance
                    })
                    current_balance = new_balance

        # Prepare detailed response
        validation_summary = {
            "total_entries": len(parsed_transactions),
            "repeated_entries": len(repeated_transactions),
            "existing_in_db": len(existing_in_db),
            "processable": len(processable_transactions),
            "unprocessable": len(unprocessable_transactions)
        }

        account_info = {
            "account_name": user_info['account_name'],
            "current_balance": str(user_info['current_balance']),
            "projected_balance": str(current_balance),
            "total_impact": str(current_balance - Decimal(str(user_info['current_balance'])))
        }

        return {
            "message": "File verification completed",
            "account_info": account_info,
            "validation_summary": validation_summary,
            "validation_details": {
                "repeated_transactions": repeated_transactions,
                "existing_in_db": existing_in_db,
                "processable_transactions": processable_transactions,
                "unprocessable_transactions": unprocessable_transactions
            }
        }

    except UnicodeDecodeError:
        logger.error("File decoding error")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File encoding error. Please ensure the file is UTF-8 encoded"
        )
    except Exception as e:
        logger.error(f"Error verifying file: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error verifying file: {str(e)}"
        )


@app.get("/")
async def read_root(current_user: dict = Depends(get_current_user)):
    return {
        "detail": "Welcome to SubTracker API",
        "user": current_user["email"]
    }