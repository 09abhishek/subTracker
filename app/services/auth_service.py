import os
import uuid
from fastapi import HTTPException, Depends, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from datetime import datetime, timedelta, date
from typing import Annotated, Optional
import mysql.connector
from mysql.connector import Error as MySQLError
from app.config import REFRESH_TOKEN_EXPIRE_DAYS, ALGORITHM, PWD_CONTEXT
from app.db import get_db
from app.logger import logger

# OAuth2 scheme
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")

# Security configurations
SECRET_KEY = os.getenv("JWT_SECRET_KEY")
if not SECRET_KEY:
    raise ValueError("JWT_SECRET_KEY environment variable is not set")

async def get_current_user(
        token: Annotated[str, Depends(oauth2_scheme)],
        db: Annotated[mysql.connector.MySQLConnection, Depends(get_db)]
):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Unauthorized users",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        # Decode the token
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            raise credentials_exception

        # Get users
        user = get_user(db, email=email)
        if user is None:
            raise credentials_exception

        # Validate that this token belongs to this users
        if not validate_token_user(db, token, user["id"]):
            logger.warning(f"Token theft attempt detected for users {email}")
            raise credentials_exception

        return user

    except JWTError as e:
        logger.error(f"JWT decode error: {e}")
        raise credentials_exception

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
            detail="Error retrieving users data"
        )
    finally:
        cursor.close()

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

def validate_token_user(db, token: str, user_id: int) -> bool:
    """Validate that the token belongs to the specified users"""
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

def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return PWD_CONTEXT.verify(plain_password, hashed_password)
    except Exception as e:
        logger.error(f"Password verification error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error verifying password"
        )


