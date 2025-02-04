from typing import List, Optional
from fastapi import APIRouter
from app.models.models import TransactionType, CategoryCreate, CategoryResponse
import mysql.connector
from fastapi import HTTPException, Depends, status
from mysql.connector import Error as MySQLError
from app.db import get_db
from app.logger import logger

router = APIRouter(
    prefix="/category",
    tags=["Transactions Categories"]
)

@router.get("/all", response_model=List[CategoryResponse])
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

@router.post("/setup", response_model=List[CategoryResponse])
async def setup_default_categories(
        db: mysql.connector.MySQLConnection = Depends(get_db)
):
    """Setup default categories if they don't exist. Preserves existing categories."""
    default_categories = [
        (1,'Salary', 'income', 'Regular employment income'),
        (2,'Investment Returns', 'income', 'Returns from mutual funds and investments'),
        (3,'Freelance', 'income', 'Freelance and project-based income'),
        (4,'Other Income', 'income', 'Miscellaneous income'),
        (5,'Deposit', 'income', 'Cash deposit to self account'),
        (6,'Food & Dining', 'expense', 'Groceries and restaurants'),
        (7,'Utilities', 'expense', 'Electricity, internet, and bills'),
        (8,'Transportation', 'expense', 'Fuel and travel expenses'),
        (9,'Health', 'expense', 'Medical and pharmacy expenses'),
        (10,'Shopping', 'expense', 'Online and offline shopping'),
        (11,'EMI & Payments', 'expense', 'Loan EMIs and credit card payments'),
        (12,'Investment', 'expense', 'Mutual funds and investments'),
        (13,'Entertainment', 'expense', 'Leisure and recreational expenses'),
        (14,'Other Expense', 'expense', 'Miscellaneous expense'),
        (15,'Internal Transfer', 'transfer', 'Account transfers')
    ]

    cursor = None
    try:
        cursor = db.cursor(dictionary=True)
        # Starting db transaction
        cursor.execute("START TRANSACTION")
        # Getting all existing categories
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

        # Commiting the db transaction
        cursor.execute("COMMIT")

        # Fetching all categories
        cursor.execute("""
            SELECT * FROM categories 
            ORDER BY type, name
        """)

        categories = cursor.fetchall()

        # Printing category count by type
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

@router.post("/add", response_model=CategoryResponse)
async def create_category(
        category: CategoryCreate,
        db: mysql.connector.MySQLConnection = Depends(get_db)
):
    """Create a new custom category if it doesn't exist"""
    cursor = None
    try:
        cursor = db.cursor(dictionary=True)

        # Checking if category already exists case-insensitive search
        cursor.execute("""
            SELECT * FROM categories 
            WHERE LOWER(name) = LOWER(%s) AND type = %s
        """, (category.name.strip(), category.type))

        existing_category = cursor.fetchone()
        if existing_category:
            return existing_category  # Return existing category instead of creating new one

        # Creating new category only if it doesn't exist
        cursor.execute("""
            INSERT INTO categories (name, type, description)
            VALUES (%s, %s, %s)
        """, (category.name.strip(), category.type, category.description))

        category_id = cursor.lastrowid
        db.commit()

        # Fetching and returning the created category
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

@router.get("/{type}", response_model=List[CategoryResponse])
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

