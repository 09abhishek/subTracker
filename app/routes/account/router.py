from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional
from fastapi import APIRouter, Query
from app.models.models import TransactionCreate, TransactionResponse, TransactionDateRangeResponse, TransactionType, \
    BalanceResponse, BalanceUpdate, BankAccountResponse, ExpenseCreate
import mysql.connector
from fastapi import HTTPException, Depends, status
from mysql.connector import Error as MySQLError
from app.db import get_db
from app.logger import logger
from app.services.auth_service import get_current_user, oauth2_scheme
from app.services.category_service import get_category_by_id
from app.services.transaction_service import update_bank_balance, get_bank_account

router = APIRouter(
    prefix="/account",
    tags=["User Account"]
)

@router.get("/balance", response_model=BalanceResponse)
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

@router.put("/accounts/balance", response_model=BankAccountResponse)
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

        # Starting transaction
        cursor.execute("START TRANSACTION")

        # Checking and lock bank account as transaction
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

        # Checking if category exists and matches transaction type
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

        # Check balance for withdrawals
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

        # Inserting into the transaction table
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

        # Commiting the transaction
        cursor.execute("COMMIT")

        # 7. Fetch the updated info and returning the updated account details
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

@router.post("/expense", response_model=TransactionResponse, status_code=status.HTTP_201_CREATED)
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

        cursor.execute("START TRANSACTION")

        # checking user's bank account and verify sufficient balance
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

        # Verifying category exists and is an expense category
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

        # Checking if sufficient balance available
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

        # Inserting transaction record
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

        # Updating bank account balance
        cursor.execute("""
            UPDATE bank_accounts 
            SET current_balance = current_balance + %s,
                updated_at = CURRENT_TIMESTAMP 
            WHERE id = %s
        """, (expense_amount, bank_account["id"]))

        cursor.execute("COMMIT")

        # Return the created transaction
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

@router.post("/transaction/", response_model=TransactionResponse)
async def create_transaction(
        transaction: TransactionCreate,
        current_user: dict = Depends(get_current_user),
        db: mysql.connector.MySQLConnection = Depends(get_db)
):
    cursor = None
    try:
        cursor = db.cursor(dictionary=True)

        # Verifying bank account belongs to user
        bank_account = get_bank_account(cursor, transaction.bank_account_id, current_user["id"])
        if not bank_account:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Bank account not found"
            )

        # Verifying category exists
        category = get_category_by_id(cursor, transaction.category_id)
        if not category:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Category not found"
            )

        # Starting db transaction
        cursor.execute("START TRANSACTION")

        # Creating db transaction
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

        # Updating bank account balance
        update_bank_balance(cursor, transaction.bank_account_id, transaction.amount)

        # Commit db transaction
        cursor.execute("COMMIT")

        # Fetching inserted transaction
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

@router.get("/transactions/", response_model=List[TransactionResponse])
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

@router.get("/transactions/by-date", response_model=TransactionDateRangeResponse)
async def get_transactions_by_date_range(
        from_date: str = Query(..., description="Start date in DD/MM/YYYY format"),
        to_date: str = Query(..., description="End date in DD/MM/YYYY format"),
        transaction_type: Optional[TransactionType] = None,
        current_user: dict = Depends(get_current_user),
        db: mysql.connector.MySQLConnection = Depends(get_db)
):
    """
    Get all transactions within a date range for the authenticated user's bank account.
    Returns both summary statistics and detailed transaction data.
    """
    cursor = None
    try:
        try:
            start_date = datetime.strptime(from_date, '%d/%m/%Y').strftime('%Y-%m-%d')
            end_date = datetime.strptime(to_date, '%d/%m/%Y').strftime('%Y-%m-%d')
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid date format. Please use DD/MM/YYYY format"
            )

        cursor = db.cursor(dictionary=True)
        cursor.execute("""
            SELECT id, account_name, current_balance 
            FROM bank_accounts 
            WHERE user_id = %s
            ORDER BY created_at ASC
            LIMIT 1
        """, (current_user["id"],))

        bank_account = cursor.fetchone()

        print("=========================")
        print(bank_account)

        if not bank_account:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No bank account found for user"
            )
        query = """
            SELECT 
                t.*,
                c.name as category_name,
                c.type as category_type,
                c.description as category_description,
                ba.account_name,
                ba.account_type,
                ba.currency
            FROM transactions t
            JOIN categories c ON t.category_id = c.id
            JOIN bank_accounts ba ON t.bank_account_id = ba.id
            WHERE t.user_id = %s
            AND t.bank_account_id = %s
            AND t.date BETWEEN %s AND %s
        """
        params = [current_user["id"], bank_account["id"], start_date, end_date]

        if transaction_type:
            query += " AND t.type = %s"
            params.append(transaction_type)

        query += " ORDER BY t.date DESC, t.id DESC"

        cursor.execute(query, params)
        transactions = cursor.fetchall()

        # Converting Decimal values to strings in transactions
        processed_transactions = []
        for transaction in transactions:
            processed_transaction = {}
            for key, value in transaction.items():
                if isinstance(value, Decimal):
                    processed_transaction[key] = str(value)
                elif isinstance(value, datetime):
                    processed_transaction[key] = value.strftime('%Y-%m-%d %H:%M:%S')
                elif isinstance(value, date):
                    processed_transaction[key] = value.strftime('%Y-%m-%d')
                else:
                    processed_transaction[key] = value
            processed_transactions.append(processed_transaction)

        summary = {
            'date_range': {
                'from': from_date,
                'to': to_date
            },
            'account_details': {
                'account_name': bank_account['account_name'],
                'current_balance': str(bank_account['current_balance']),
                'account_id': bank_account['id']
            },
            'transaction_statistics': {
                'total_income': str(sum(t['amount'] for t in transactions if t['type'] == 'income')),
                'total_expense': str(abs(sum(t['amount'] for t in transactions if t['type'] == 'expense'))),
                'total_transfers': str(sum(t['amount'] for t in transactions if t['type'] == 'transfer')),
                'transaction_count': len(transactions)
            }
        }

        # Category-wise totals
        category_totals = {}
        for transaction in transactions:
            category = transaction['category_name']
            amount = transaction['amount']
            if category not in category_totals:
                category_totals[category] = Decimal('0')
            category_totals[category] += amount

        summary['category_totals'] = {
            category: str(total)
            for category, total in category_totals.items()
        }

        return {
            "summary": summary,
            "transactions": processed_transactions
        }

    except MySQLError as e:
        logger.error(f"Database error in get_transactions_by_date_range: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error: {str(e)}"
        )
    finally:
        if cursor:
            cursor.close()

