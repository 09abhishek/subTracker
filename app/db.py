import os
from fastapi import HTTPException, status
import mysql.connector
from mysql.connector import Error as MySQLError
from dotenv import load_dotenv
from app.logger import logger

load_dotenv()

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
