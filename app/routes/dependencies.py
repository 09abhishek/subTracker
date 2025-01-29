from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from typing import Annotated
import mysql.connector
import logging
from .token import TokenManager
from ..database import get_db
from ..config import settings

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize OAuth2 scheme
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")

# Initialize token manager
token_manager = TokenManager(secret_key=settings.SECRET_KEY)


async def get_current_user(
        token: Annotated[str, Depends(oauth2_scheme)],
        db: Annotated[mysql.connector.MySQLConnection, Depends(get_db)]
):
    """Dependency to get current authenticated user"""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Unauthorized user",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        # Verify token signature and get payload
        payload = token_manager.verify_token(token)
        if not payload:
            raise credentials_exception

        email = payload.get("sub")
        if not email:
            raise credentials_exception

        # Get user from database
        user = get_user(db, email=email)
        if not user:
            raise credentials_exception

        # Validate token is still active for this user
        if not token_manager.validate_access_token(db, token, user["id"]):
            raise credentials_exception

        return user

    except Exception as e:
        logger.error(f"Authentication error: {e}")
        raise credentials_exception


def get_user(db: mysql.connector.MySQLConnection, email: str):
    """Get user from database by email"""
    try:
        cursor = db.cursor(dictionary=True)
        cursor.execute(
            "SELECT * FROM users WHERE email = %s",
            (email,)
        )
        return cursor.fetchone()
    finally:
        cursor.close()