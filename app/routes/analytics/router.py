from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, Query
import mysql.connector
from fastapi import HTTPException, Depends, status
from mysql.connector import Error as MySQLError
from app.db import get_db
from app.logger import logger
from app.services.auth_service import get_current_user

router = APIRouter(
    prefix="/analytics",
    tags=["Transactions Analytics"]
)

@router.get("/top-spending")
async def get_top_spending(
    from_date: str = Query(..., description="Start date in DD/MM/YYYY format"),
    to_date: str = Query(..., description="End date in DD/MM/YYYY format"),
    current_user: dict = Depends(get_current_user),
    db: mysql.connector.MySQLConnection = Depends(get_db)
):
    """
    Get top spending data grouped by category within a date range.
    Returns data suitable for pie chart visualization.
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

        # Get spending data grouped by category
        query = """
            SELECT 
                c.name as category,
                ABS(SUM(t.amount)) as total_amount,
                COUNT(t.id) as transaction_count,
                MIN(t.date) as first_transaction,
                MAX(t.date) as last_transaction
            FROM transactions t
            JOIN categories c ON t.category_id = c.id
            WHERE t.user_id = %s 
                AND t.type = 'expense'
                AND t.date BETWEEN %s AND %s
            GROUP BY c.name
            ORDER BY total_amount DESC
        """

        cursor.execute(query, (current_user["id"], start_date, end_date))
        spending_data = cursor.fetchall()

        if not spending_data:
            return {
                "message": "No spending data found for the specified date range",
                "date_range": {"from": from_date, "to": to_date},
                "total_spending": "0",
                "categories": []
            }

        # Calculate total spending
        total_spending = sum(float(item['total_amount']) for item in spending_data)

        # Process the data for visualization
        categories = []
        for item in spending_data:
            categories.append({
                "category": item['category'],
                "amount": str(item['total_amount']),
                # "percentage": round((float(item['total_amount']) / total_spending) * 100, 2),
                "transaction_count": item['transaction_count'],
                "first_transaction": item['first_transaction'].strftime('%Y-%m-%d'),
                "last_transaction": item['last_transaction'].strftime('%Y-%m-%d')
            })

        return {
            "message": "Top spending analysis retrieved successfully",
            "date_range": {
                "from": from_date,
                "to": to_date
            },
            "total_spending": str(total_spending),
            "category_count": len(categories),
            "categories": categories
        }

    except MySQLError as e:
        logger.error(f"Database error in get_top_spending: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error: {str(e)}"
        )
    finally:
        if cursor:
            cursor.close()


@router.get("/monthly-spending-trend")
async def get_monthly_spending_trend(
        current_user: dict = Depends(get_current_user),
        db: mysql.connector.MySQLConnection = Depends(get_db)
):
    """
    Get spending trend comparison between current month and previous month.
    Returns daily spending data suitable for line chart visualization.
    """
    cursor = None
    try:
        # Calculate date ranges for current and previous month
        today = datetime.now()
        current_month_start = datetime(today.year, today.month, 1)
        current_month_end = (current_month_start + timedelta(days=32)).replace(day=1) - timedelta(days=1)

        prev_month_start = (current_month_start - timedelta(days=1)).replace(day=1)
        prev_month_end = current_month_start - timedelta(days=1)

        cursor = db.cursor(dictionary=True)

        # Query to get daily spending for both months
        query = """
            SELECT 
                DATE(t.date) as date,
                ABS(SUM(t.amount)) as total_spending,
                COUNT(t.id) as transaction_count
            FROM transactions t
            WHERE t.user_id = %s 
                AND t.type = 'expense'
                AND t.date BETWEEN %s AND %s
            GROUP BY DATE(t.date)
            ORDER BY date ASC
        """

        # Get current month data
        cursor.execute(query, (
            current_user["id"],
            current_month_start.strftime('%Y-%m-%d'),
            current_month_end.strftime('%Y-%m-%d')
        ))
        current_month_data = cursor.fetchall()

        # Get previous month data
        cursor.execute(query, (
            current_user["id"],
            prev_month_start.strftime('%Y-%m-%d'),
            prev_month_end.strftime('%Y-%m-%d')
        ))
        prev_month_data = cursor.fetchall()

        # Process data for response
        def process_monthly_data(data, month_start, month_end):
            daily_data = {}
            current_date = month_start

            # Initialize all days with zero spending
            while current_date <= month_end:
                daily_data[current_date.strftime('%Y-%m-%d')] = {
                    "date": current_date.strftime('%Y-%m-%d'),
                    "total_spending": "0",
                    "transaction_count": 0
                }
                current_date += timedelta(days=1)

            # Update with actual spending data
            for entry in data:
                date_str = entry['date'].strftime('%Y-%m-%d')
                daily_data[date_str] = {
                    "date": date_str,
                    "total_spending": str(entry['total_spending']),
                    "transaction_count": entry['transaction_count']
                }

            return list(daily_data.values())

        current_month_processed = process_monthly_data(
            current_month_data,
            current_month_start,
            current_month_end
        )
        prev_month_processed = process_monthly_data(
            prev_month_data,
            prev_month_start,
            prev_month_end
        )

        # Calculate summary statistics
        def calculate_summary(data):
            total_spending = sum(float(day['total_spending']) for day in data)
            total_transactions = sum(day['transaction_count'] for day in data)
            days_with_spending = sum(1 for day in data if float(day['total_spending']) > 0)

            return {
                "total_spending": str(total_spending),
                "average_daily_spending": str(total_spending / len(data)) if len(data) > 0 else "0",
                "total_transactions": total_transactions,
                "days_with_spending": days_with_spending,
                "days_without_spending": len(data) - days_with_spending
            }

        return {
            "current_month": {
                "start_date": current_month_start.strftime('%Y-%m-%d'),
                "end_date": current_month_end.strftime('%Y-%m-%d'),
                "summary": calculate_summary(current_month_processed),
                "daily_data": current_month_processed
            },
            "previous_month": {
                "start_date": prev_month_start.strftime('%Y-%m-%d'),
                "end_date": prev_month_end.strftime('%Y-%m-%d'),
                "summary": calculate_summary(prev_month_processed),
                "daily_data": prev_month_processed
            },
            "comparison": {
                "current_month": current_month_start.strftime('%B %Y'),
                "previous_month": prev_month_start.strftime('%B %Y')
            }
        }

    except MySQLError as e:
        logger.error(f"Database error in get_monthly_spending_trend: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error: {str(e)}"
        )
    finally:
        if cursor:
            cursor.close()


@router.get("/weekly-spending")
async def get_weekly_spending(
        weeks: int = Query(4, description="Number of recent weeks to fetch", ge=1, le=12),
        current_user: dict = Depends(get_current_user),
        db: mysql.connector.MySQLConnection = Depends(get_db)
):
    """
    Get weekly spending data for the specified number of recent weeks.
    Returns data suitable for bar chart visualization.
    """
    cursor = None
    try:
        # Calculate date ranges
        today = datetime.now()
        end_date = today
        start_date = end_date - timedelta(weeks=weeks)

        cursor = db.cursor(dictionary=True)

        # Query to get daily spending data
        query = """
            SELECT 
                DATE(t.date) as date,
                ABS(SUM(t.amount)) as daily_total,
                COUNT(t.id) as transaction_count,
                GROUP_CONCAT(DISTINCT c.name) as categories
            FROM transactions t
            JOIN categories c ON t.category_id = c.id
            WHERE t.user_id = %s 
                AND t.type = 'expense'
                AND t.date BETWEEN %s AND %s
            GROUP BY DATE(t.date)
            ORDER BY date ASC
        """

        cursor.execute(query, (
            current_user["id"],
            start_date.strftime('%Y-%m-%d'),
            end_date.strftime('%Y-%m-%d')
        ))
        daily_data = cursor.fetchall()

        # Process data week by week
        weekly_data = []
        current_week_start = start_date

        while current_week_start <= end_date:
            week_end = min(current_week_start + timedelta(days=6), end_date)

            # Filter daily data for current week
            current_week_start_date = current_week_start.date()
            week_end_date = week_end.date()
            week_entries = [
                entry for entry in daily_data
                if current_week_start_date <= entry['date'] <= week_end_date
            ]

            # Calculate weekly totals
            total_spending = sum(float(entry['daily_total']) for entry in week_entries)
            total_transactions = sum(entry['transaction_count'] for entry in week_entries)

            # Get unique categories for the week
            categories = set()
            for entry in week_entries:
                if entry['categories']:
                    categories.update(entry['categories'].split(','))

            # Calculate daily averages
            days_with_spending = len(week_entries)
            daily_avg = total_spending / 7  # Average over full week

            weekly_data.append({
                "week_start": current_week_start.strftime('%Y-%m-%d'),
                "week_end": week_end.strftime('%Y-%m-%d'),
                "week_label": f"Week {len(weekly_data) + 1}",
                "total_spending": str(total_spending),
                "daily_average": str(daily_avg),
                "total_transactions": total_transactions,
                "days_with_spending": days_with_spending,
                "days_without_spending": 7 - days_with_spending,
                "categories": list(categories)
            })

            current_week_start += timedelta(days=7)

        # Calculate overall statistics
        total_spending = sum(float(week['total_spending']) for week in weekly_data)
        total_transactions = sum(week['total_transactions'] for week in weekly_data)

        return {
            "summary": {
                "date_range": {
                    "start": start_date.strftime('%Y-%m-%d'),
                    "end": end_date.strftime('%Y-%m-%d')
                },
                "total_weeks": len(weekly_data),
                "total_spending": str(total_spending),
                "total_transactions": total_transactions,
                "weekly_average": str(total_spending / len(weekly_data)) if weekly_data else "0"
            },
            "weekly_data": weekly_data
        }

    except MySQLError as e:
        logger.error(f"Database error in get_weekly_spending: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error: {str(e)}"
        )
    finally:
        if cursor:
            cursor.close()


@router.get("/income-expense")
async def get_monthly_income_expense(
        month: Optional[str] = Query(None, description="Month in DD/MM/YYYY format"),
        current_user: dict = Depends(get_current_user),
        db: mysql.connector.MySQLConnection = Depends(get_db)
):
    cursor = None
    try:
        if month:
            try:
                parsed_date = datetime.strptime(month, '%d/%m/%Y')
                selected_month = parsed_date.month
                year = parsed_date.year
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid date format. Please use DD/MM/YYYY"
                )
        else:
            current_date = datetime.now()
            selected_month = current_date.month
            year = current_date.year

        cursor = db.cursor(dictionary=True)

        query = """
            SELECT 
                c.type as transaction_type,
                ABS(SUM(t.amount)) as amount
            FROM transactions t
            JOIN categories c ON t.category_id = c.id
            WHERE t.user_id = %s 
                AND MONTH(t.date) = %s 
                AND YEAR(t.date) = %s
                AND c.type IN ('income', 'expense')
            GROUP BY c.type
        """

        cursor.execute(query, (current_user["id"], selected_month, year))
        transactions = cursor.fetchall()

        monthly_totals = {
            'total_income': 0,
            'total_expense': 0
        }

        for transaction in transactions:
            amount = float(transaction['amount'])
            tx_type = transaction['transaction_type']
            monthly_totals[f'total_{tx_type}'] += amount

        net_amount = monthly_totals['total_income'] - monthly_totals['total_expense']

        return {
            "month": datetime(year, selected_month, 1).strftime('%B %Y'),
            "total_income": str(monthly_totals['total_income']),
            "total_expense": str(monthly_totals['total_expense']),
            "net_amount": str(net_amount),
            "savings_rate": round((monthly_totals['total_income'] - monthly_totals['total_expense']) / monthly_totals[
                'total_income'] * 100 if monthly_totals['total_income'] > 0 else 0, 2)
        }

    except MySQLError as e:
        logger.error(f"Database error in get_monthly_income_expense: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error: {str(e)}"
        )
    finally:
        if cursor:
            cursor.close()

