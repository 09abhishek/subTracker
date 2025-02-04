from datetime import timedelta, datetime
from fastapi import APIRouter
from fastapi.security import OAuth2PasswordRequestForm
from jose import jwt, JWTError
from app.config import ACCESS_TOKEN_EXPIRE_MINUTES, REFRESH_TOKEN_EXPIRE_DAYS, ALGORITHM
import mysql.connector
from fastapi import HTTPException, Depends, status, Response
from typing import Annotated
from mysql.connector import Error as MySQLError
from app.db import get_db
from app.logger import logger
from app.models.models import Token
from app.services.auth_service import authenticate_user, create_access_token, store_tokens, oauth2_scheme, \
    get_current_user, SECRET_KEY

router = APIRouter(
    prefix="/auth",
    tags=["Authentication"]
)

@router.post("/login", response_model=Token)
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

@router.post("/logout")
async def logout(
        token: Annotated[str, Depends(oauth2_scheme)],
        current_user: dict = Depends(get_current_user),
        db: mysql.connector.MySQLConnection = Depends(get_db)
):
    try:
        cursor = db.cursor()
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

@router.post("/refresh-token", response_model=Token)
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