from datetime import datetime
from decimal import Decimal
from http.client import HTTPException
from fastapi import FastAPI, UploadFile, File, HTTPException, Depends, status, Response
from fastapi.middleware.cors import CORSMiddleware
import mysql.connector
from app.db import get_db
from app.logger import logger


async def process_transactions(
        transactions: list,
        user_email: str,
        db: mysql.connector.MySQLConnection = Depends(get_db)
) -> dict:
    """
    Process a list of transactions from the ledger file.
    Validates balance and creates transaction records.

    Args:
        transactions: List of transaction dictionaries
        user_email: Email from JWT token
        db: Database connection

    Returns:
        Dict containing successful and failed transactions
    """
    cursor = None
    results = {
        "successful": [],
        "failed": [],
        "total_processed": 0,
        "total_success": 0,
        "total_failed": 0
    }

    try:
        cursor = db.cursor(dictionary=True)

        # Get user and bank account information
        cursor.execute("""
            SELECT u.id as user_id, b.id as bank_account_id, b.current_balance
            FROM users u
            JOIN bank_accounts b ON u.id = b.user_id
            WHERE u.email = %s
            ORDER BY b.created_at ASC
            LIMIT 1
        """, (user_email,))

        user_info = cursor.fetchone()
        if not user_info:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User or bank account not found"
            )

        # Start transaction
        cursor.execute("START TRANSACTION")

        # Convert current balance to Decimal
        current_balance = user_info['current_balance']  # Already Decimal from MySQL

        # Process each transaction
        for tx in transactions:
            results['total_processed'] += 1

            try:
                # Convert amount to Decimal
                tx_amount = Decimal(str(tx['amount']))

                # Calculate new balance
                amount = tx_amount if tx['type'] == 'income' else -tx_amount
                new_balance = current_balance + amount

                # For expenses, verify sufficient balance
                if tx['type'] == 'expense' and new_balance < 0:
                    raise ValueError(
                        f"Insufficient balance for transaction: {tx['description']}. "
                        f"Required: {tx_amount}, Available: {current_balance}"
                    )

                # Insert transaction record
                cursor.execute("""
                    INSERT INTO transactions (
                        user_id,
                        bank_account_id,
                        category_id,
                        date,
                        description,
                        amount,
                        type,
                        debit_account,
                        credit_account,
                        source
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'import')
                """, (
                    user_info['user_id'],
                    user_info['bank_account_id'],
                    tx['category_id'],
                    tx['date'],
                    tx['description'],
                    str(amount),  # Convert Decimal to string for MySQL
                    tx['type'],
                    tx['debit_account'],
                    tx['credit_account']
                ))

                # Update current balance
                current_balance = new_balance

                # Add to successful transactions
                tx['status'] = 'success'
                tx['processed_amount'] = str(amount)  # Include processed amount in response
                results['successful'].append(tx)
                results['total_success'] += 1

            except Exception as e:
                # Add to failed transactions with error message
                tx['status'] = 'failed'
                tx['error'] = str(e)
                results['failed'].append(tx)
                results['total_failed'] += 1
                logger.error(f"Failed to process transaction: {tx['description']}, Error: {str(e)}")

        # Update final balance if there were any successful transactions
        if results['successful']:
            cursor.execute("""
                UPDATE bank_accounts 
                SET current_balance = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
            """, (str(current_balance), user_info['bank_account_id']))  # Convert Decimal to string

        # Commit transaction if any were successful
        if results['total_success'] > 0:
            cursor.execute("COMMIT")
            logger.info(f"Successfully processed {results['total_success']} transactions")
        else:
            cursor.execute("ROLLBACK")
            logger.warning("No transactions were processed successfully, rolling back")

        return results

    except Exception as e:
        if cursor:
            cursor.execute("ROLLBACK")
        logger.error(f"Error processing transactions: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error processing transactions: {str(e)}"
        )
    finally:
        if cursor:
            cursor.close()


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
