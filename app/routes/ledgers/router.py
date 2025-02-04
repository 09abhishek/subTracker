from datetime import datetime, timedelta
from fastapi import FastAPI, APIRouter, UploadFile, File, HTTPException, Depends, status, Response, Query
import mysql.connector
from app.db import get_db
from app.services.auth_service import get_current_user
from app.logger import logger
from app.services.ledger_parser import parse_ledger_entries, process_transactions
from app.services.transaction_validator import TransactionValidator

router = APIRouter(
    prefix="/ledger",
    tags=["Ledgers"]
)

@router.post("/upload")
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
        content = await file.read()
        ledger_text = content.decode('utf-8')
        cursor = db.cursor(dictionary=True, buffered=True)
        parsed_transactions = parse_ledger_entries(ledger_text, cursor)

        results = await process_transactions(
            transactions=parsed_transactions,
            user_email=current_user['email'],
            db=db
        )

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


@router.post("/verify-file")
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

        with TransactionValidator(db, current_user['id']) as validator:
            # Get user's bank account info
            user_info = validator.get_user_balance(current_user['email'])
            if not user_info:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="User or bank account not found"
                )

            # Checking repeated transactions
            repeated_transactions, unique_transactions = validator.find_repeated_transactions(parsed_transactions)

            current_balance = Decimal(str(user_info['current_balance']))
            existing_in_db = []
            processable_transactions = []
            unprocessable_transactions = []

            # Validating unique transaction one by one
            for tx in unique_transactions:
                # Convert amount to Decimal
                tx_amount = Decimal(str(tx['amount']))

                # Checking if transaction exists in DB
                if validator.check_existing_transactions(tx['date'], tx_amount):
                    existing_in_db.append({
                        **tx,
                        "validation_message": "Transaction already exists in database"
                    })
                    continue

                # Calculating balance impact here
                amount = tx_amount if tx['type'] == 'income' else -tx_amount
                new_balance = current_balance + amount

                # Validating based on transaction type and balance
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

@router.get("/export")
async def export_ledger(
        from_date: str = Query(..., description="Start date in DD/MM/YYYY format"),
        to_date: str = Query(..., description="End date in DD/MM/YYYY format"),
        current_user: dict = Depends(get_current_user),
        db: mysql.connector.MySQLConnection = Depends(get_db)
):
    """
    Generate a formatted ledger file from transactions within the specified date range.
    Returns a downloadable .ledger file.
    """
    cursor = None
    try:
        # Convert dates to required format
        try:
            start_date = datetime.strptime(from_date, '%d/%m/%Y').strftime('%Y-%m-%d')
            end_date = datetime.strptime(to_date, '%d/%m/%Y').strftime('%Y-%m-%d')
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid date format. Please use DD/MM/YYYY format"
            )

        cursor = db.cursor(dictionary=True)

        # Get all transactions for the date range with category and account details
        query = """
            SELECT 
                t.*,
                c.name as category_name,
                c.type as category_type,
                ba.account_name,
                ba.currency
            FROM transactions t
            JOIN categories c ON t.category_id = c.id
            JOIN bank_accounts ba ON t.bank_account_id = ba.id
            WHERE t.user_id = %s
            AND t.date BETWEEN %s AND %s
            ORDER BY t.date ASC, t.id ASC
        """

        cursor.execute(query, (current_user["id"], start_date, end_date))
        transactions = cursor.fetchall()

        if not transactions:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No transactions found for the specified date range"
            )

        # Generate ledger content
        ledger_content = []
        for tx in transactions:
            # Format date in YYYY/MM/DD format
            tx_date = tx['date'].strftime('%Y/%m/%d')

            # Format description
            description = tx['description']
            max_desc_length = 50  # Maximum length for description on first line

            # Create entry list
            entry = []

            # Handle description formatting
            if len(description) > max_desc_length:
                # First line with date and first part of description
                entry.append(f"{tx_date} {description[:max_desc_length]}")
                # Continue description on next line with proper indentation
                remaining_desc = description[max_desc_length:]
                entry.append(f"    {remaining_desc}")
            else:
                # Single line for date and description
                entry.append(f"{tx_date} {description}")

            # Format amount with thousand separator and decimal places
            amount = abs(tx['amount'])
            amount_str = f"₹{amount:,.2f}"

            # Special handling for Cash Deposit transactions
            if description.lower().startswith('cash deposit'):
                bank_line = f"    Assets:Banking:{tx['account_name']}"
                entry.append(f"{bank_line}{' ' * (80 - len(bank_line) - len(amount_str))}{amount_str}")
                entry.append(f"    Income:Deposit")
            else:
                # Regular transaction handling
                debit_line = f"    {tx['debit_account']}"
                entry.append(f"{debit_line}{' ' * (80 - len(debit_line) - len(amount_str))}{amount_str}")
                entry.append(f"    {tx['credit_account']}")

            # Add empty line between transactions
            entry.append("")
            ledger_content.append("\n".join(entry))

        # Combine all entries
        full_ledger = "\n".join(ledger_content)

        # Format the current date for the filename
        file_date = datetime.now().strftime('%Y%m%d')
        filename = f"transactions_{file_date}.ledger"

        headers = {
            'Content-Disposition': f'attachment; filename="{filename}"',
            'Access-Control-Expose-Headers': 'Content-Disposition',
            'Content-Type': 'application/octet-stream'
        }

        # Return the response that will trigger browser download
        return Response(
            content=full_ledger,
            headers=headers,
            media_type="application/octet-stream"
        )

    except Exception as e:
        logger.error(f"Error generating ledger file: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error generating ledger file: {str(e)}"
        )
    finally:
        if cursor:
            cursor.close()

