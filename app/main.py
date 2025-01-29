import uuid
import os
from fastapi import FastAPI, UploadFile, File, HTTPException, Depends, status, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from passlib.context import CryptContext
from datetime import datetime, timedelta, date
from typing import Optional, List, Annotated
from decimal import Decimal
import mysql.connector
from mysql.connector import Error as MySQLError
from dotenv import load_dotenv
import logging
from typing import Optional
import json

from app.models import BalanceResponse, ExpenseCreate, BalanceUpdate, TransactionType, CategoryBase, UserResponse, \
    UserCreate, TransactionResponse, TransactionCreate, BankAccountResponse, CategoryResponse, CategoryCreate, Token

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Security configurations
SECRET_KEY = os.getenv("JWT_SECRET_KEY")
if not SECRET_KEY:
    raise ValueError("JWT_SECRET_KEY environment variable is not set")

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30
REFRESH_TOKEN_EXPIRE_DAYS = 30

# Password hashing configuration
pwd_context = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto",
    bcrypt__rounds=12
)

# OAuth2 scheme
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")

# Create FastAPI app instance
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

# Database configuration
DB_CONFIG = {
    'host': os.getenv('DB_HOST', '127.0.0.1'),
    'user': os.getenv('DB_USER', 'root'),
    'password': os.getenv('DB_PASSWORD', 'rootpassword'),
    'database': os.getenv('DB_NAME', 'sub_tracker'),
    'port': int(os.getenv('DB_PORT', '3306'))
}

# Database connection
def get_db():
    try:
        logger.info("Attempting to connect to the database...")
        db = mysql.connector.connect(**DB_CONFIG)
        logger.info("Database connection successful!")
        yield db
    except MySQLError as err:
        logger.error(f"Database connection error: {err}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not connect to the database"
        )
    finally:
        try:
            if 'db' in locals():
                logger.info("Closing database connection...")
                db.close()
                logger.info("Database connection closed successfully!")
        except Exception as e:
            logger.error(f"Error closing database connection: {e}")


# Helper Functions
def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return pwd_context.verify(plain_password, hashed_password)
    except Exception as e:
        logger.error(f"Password verification error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error verifying password"
        )


def hash_password(password: str) -> str:
    try:
        return pwd_context.hash(password)
    except Exception as e:
        logger.error(f"Password hashing error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error processing password"
        )


def get_user(db, email: str):
    try:
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
        user = cursor.fetchone()
        return user
    except MySQLError as e:
        logger.error(f"Database error in get_user: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error retrieving user data"
        )
    finally:
        cursor.close()

async def get_current_user(
        token: Annotated[str, Depends(oauth2_scheme)],
        db: Annotated[mysql.connector.MySQLConnection, Depends(get_db)]
):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Unauthorized user",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        # Decode the token
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            raise credentials_exception

        # Get user
        user = get_user(db, email=email)
        if user is None:
            raise credentials_exception

        # Validate that this token belongs to this user
        if not validate_token_user(db, token, user["id"]):
            logger.warning(f"Token theft attempt detected for user {email}")
            raise credentials_exception

        return user

    except JWTError as e:
        logger.error(f"JWT decode error: {e}")
        raise credentials_exception


def authenticate_user(db, email: str, password: str):
    user = get_user(db, email)
    if not user:
        return False
    if not verify_password(password, user['password_hash']):
        return False
    return user

def create_refresh_token(db: mysql.connector.MySQLConnection, user_id: int, access_token: str) -> str:
    """Create a new refresh token and store both tokens in the database"""
    try:
        expires_at = datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
        refresh_token = jwt.encode(
            {
                "sub": str(user_id),
                "jti": str(uuid.uuid4()),
                "exp": expires_at.timestamp()
            },
            SECRET_KEY,
            algorithm=ALGORITHM
        )

        cursor = db.cursor()
        cursor.execute("""
            INSERT INTO auth_tokens (user_id, access_token, refresh_token, expires_at)
            VALUES (%s, %s, %s, %s)
        """, (user_id, access_token, refresh_token, expires_at))
        db.commit()
        return refresh_token
    except MySQLError as e:
        logger.error(f"Database error in create_refresh_token: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error creating refresh token"
        )
    finally:
        if cursor:
            cursor.close()


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


def create_bank_account(cursor, user_id: int, bank_name: str) -> int:
    """Creates a default bank account for a user and returns the account ID"""
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


def get_category_by_id(cursor, category_id: int):
    cursor.execute(
        "SELECT * FROM categories WHERE id = %s",
        (category_id,)
    )
    return cursor.fetchone()


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


# Endpoints
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


def is_token_valid(db, token: str) -> bool:
    """Check if a token exists and is not expired"""
    try:
        cursor = db.cursor()
        cursor.execute("""
            SELECT 1 FROM auth_tokens 
            WHERE refresh_token = %s AND expires_at > NOW()
            LIMIT 1
        """, (token,))
        return cursor.fetchone() is not None
    finally:
        cursor.close()

def is_token_valid(db, token: str) -> bool:
    """Check if a token exists and is not expired"""
    try:
        cursor = db.cursor()
        cursor.execute("""
            SELECT 1 FROM auth_tokens 
            WHERE refresh_token = %s AND expires_at > NOW()
            LIMIT 1
        """, (token,))
        return cursor.fetchone() is not None
    finally:
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


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    try:
        to_encode = data.copy()
        expire = datetime.utcnow() + (expires_delta or timedelta(minutes=15))
        to_encode.update({"exp": expire})
        # Add a unique token ID to help track tokens
        to_encode.update({"jti": str(uuid.uuid4())})
        return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    except Exception as e:
        logger.error(f"Token creation error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error creating access token"
        )


def store_tokens(db, user_id: int, access_token: str, refresh_token: str, expires_at: datetime):
    """Store both access and refresh tokens in the database"""
    try:
        cursor = db.cursor()
        cursor.execute("""
            INSERT INTO auth_tokens (user_id, access_token, refresh_token, expires_at)
            VALUES (%s, %s, %s, %s)
        """, (user_id, access_token, refresh_token, expires_at))
        db.commit()
    except MySQLError as e:
        logger.error(f"Error storing tokens: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error storing authentication tokens"
        )
    finally:
        cursor.close()


def validate_token_user(db, token: str, user_id: int) -> bool:
    """Validate that the token belongs to the specified user"""
    try:
        cursor = db.cursor()
        cursor.execute("""
            SELECT EXISTS(
                SELECT 1 FROM auth_tokens 
                WHERE (access_token = %s OR refresh_token = %s)
                AND user_id = %s 
                AND expires_at > NOW()
            )
        """, (token, token, user_id))
        return cursor.fetchone()[0]
    finally:
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
        file: UploadFile = File(...)
):
    """
    Upload a .ledger file and return formatted JSON of transactions.
    """
    logger.info(f"Processing file: {file.filename}")

    # Validate file extension
    if not file.filename.endswith('.ledger'):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file format. Please upload a .ledger file"
        )

    try:
        # Read file content
        content = await file.read()
        ledger_text = content.decode('utf-8')
        transactions = parse_ledger_entries(ledger_text)

        # Process the transactions
        result = await process_transactions(
            db=db,
            user_id=user_id,
            bank_account_id=bank_account_id,
            transactions=transactions["transactions"]
        )

        return result

        logger.info(f"Successfully parsed {len(transactions)} transactions")
        return {
            "transactions": transactions
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


def determine_category(account: str, trans_type: str) -> int:
    """Map ledger account to category ID"""
    category_mappings = {
        "income": {
            "Salary": 1,
            "MF": 2,
            "Investment": 2,
            "Freelance": 3,
            "Other": 4
        },
        "expense": {
            "Food": 5,
            "Grocery": 5,
            "Dining": 5,
            "Utilities": 6,
            "Electricity": 6,
            "Internet": 6,
            "Transport": 7,
            "Fuel": 7,
            "Health": 8,
            "Pharmacy": 8,
            "Shopping": 9,
            "EMI": 10,
            "Loan": 10,
            "Investment": 11
        }
    }

    account_parts = account.split(':')

    for part in account_parts:
        for category, id in category_mappings[trans_type].items():
            if category.lower() in part.lower():
                return id

    return 4 if trans_type == "income" else 9


def parse_ledger_entries(ledger_text: str) -> list:
    """Parse .ledger file into structured JSON format"""
    transactions = []
    lines = ledger_text.splitlines()

    current_transaction = None
    current_accounts = []
    current_amount = None

    for line in lines:
        # Skip empty lines
        if not line.strip():
            continue

        # New transaction starts with a date
        if line[0].isdigit():
            # Save previous transaction if exists
            if current_transaction and len(current_accounts) == 2:
                trans_type = "income" if "Income:" in current_accounts[1] else "expense"

                transactions.append({
                    "date": current_transaction["date"],
                    "description": current_transaction["description"],
                    "type": trans_type,
                    "amount": float(current_amount),
                    "debit_account": current_accounts[0],
                    "credit_account": current_accounts[1],
                    "category_id": determine_category(
                        current_accounts[1] if trans_type == "income" else current_accounts[0],
                        trans_type
                    )
                })

            # Parse new transaction header
            date_str = line[:10]
            description = line[10:].strip()

            current_transaction = {
                "date": datetime.strptime(date_str, '%Y/%m/%d').strftime('%Y-%m-%d'),
                "description": description
            }
            current_accounts = []
            current_amount = None

        # Parse account postings
        elif line.startswith(' ') and current_transaction:
            parts = [p.strip() for p in line.split('  ') if p.strip()]

            if not parts:
                continue

            account = parts[0]
            current_accounts.append(account)

            # Extract amount if present
            if len(parts) > 1:
                amount_str = parts[-1].replace('₹', '').replace(',', '')
                try:
                    current_amount = float(amount_str)
                except ValueError:
                    pass

    # Don't forget the last transaction
    if current_transaction and len(current_accounts) == 2:
        trans_type = "income" if "Income:" in current_accounts[1] else "expense"

        transactions.append({
            "date": current_transaction["date"],
            "description": current_transaction["description"],
            "type": trans_type,
            "amount": float(current_amount),
            "credit_account": current_accounts[0],
            "debit_account": current_accounts[1],
            "category_id": determine_category(
                current_accounts[1] if trans_type == "income" else current_accounts[0],
                trans_type
            )
        })

    logger.debug(f"Parsed transactions: {transactions}")
    return transactions














@app.get("/")
async def read_root(current_user: dict = Depends(get_current_user)):
    return {
        "detail": "Welcome to SubTracker API",
        "user": current_user["email"]
    }