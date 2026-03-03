"""
Authentication: JWT access tokens + DB-backed refresh tokens + password reset.
"""
import os
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, status, APIRouter
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
import bcrypt
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from database import User, RefreshToken, PasswordResetToken, get_db

# ─── Config ───────────────────────────────────────────────────────────────────
SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError("SECRET_KEY environment variable is not set.")
if len(SECRET_KEY) < 32:
    raise RuntimeError("SECRET_KEY must be at least 32 characters.")

ALGORITHM                    = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES  = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES",  "15"))   # short-lived
REFRESH_TOKEN_EXPIRE_DAYS    = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS",    "30"))
RESET_TOKEN_EXPIRE_MINUTES   = int(os.getenv("RESET_TOKEN_EXPIRE_MINUTES",   "60"))

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


# ─── Helpers ──────────────────────────────────────────────────────────────────
def _hash_token(raw: str) -> str:
    """SHA-256 hash for storing tokens in DB without exposing raw values."""
    return hashlib.sha256(raw.encode()).hexdigest()


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def create_access_token(user_id: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    return jwt.encode({"sub": user_id, "exp": expire}, SECRET_KEY, algorithm=ALGORITHM)


async def create_refresh_token(user_id: str, db: AsyncSession) -> str:
    """Generate a random refresh token, store its hash in DB, return raw token."""
    raw        = secrets.token_urlsafe(64)
    token_hash = _hash_token(raw)
    expires_at = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)

    db.add(RefreshToken(user_id=user_id, token_hash=token_hash, expires_at=expires_at))
    await db.commit()
    return raw


# ─── Pydantic schemas ─────────────────────────────────────────────────────────
class UserRegister(BaseModel):
    email: str
    password: str

class UserOut(BaseModel):
    id: str
    email: str
    is_active: bool
    created_at: datetime
    model_config = {"from_attributes": True}

class TokenResponse(BaseModel):
    access_token:  str
    refresh_token: str
    token_type:    str = "bearer"
    user:          UserOut

class RefreshRequest(BaseModel):
    refresh_token: str

class ForgotPasswordRequest(BaseModel):
    email: str

class ResetPasswordRequest(BaseModel):
    token:        str
    new_password: str


# ─── FastAPI dependencies ─────────────────────────────────────────────────────
async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload  = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        if not user_id:
            raise exc
    except JWTError:
        raise exc

    result = await db.execute(select(User).where(User.id == user_id))
    user   = result.scalar_one_or_none()
    if not user:
        raise exc
    return user


async def require_active_user(user: User = Depends(get_current_user)) -> User:
    if not user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    return user


# ─── Auth router ──────────────────────────────────────────────────────────────
auth_router = APIRouter(prefix="/api/auth", tags=["auth"])


@auth_router.post("/register", response_model=TokenResponse, status_code=201)
async def register(body: UserRegister, db: AsyncSession = Depends(get_db)):
    existing = await db.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Email already registered")
    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    if len(body.password) > 72:
        raise HTTPException(status_code=400, detail="Password must be under 72 characters")

    user = User(email=body.email, hashed_password=hash_password(body.password))
    db.add(user)
    await db.commit()
    await db.refresh(user)

    access_token  = create_access_token(user.id)
    refresh_token = await create_refresh_token(user.id, db)
    return TokenResponse(access_token=access_token, refresh_token=refresh_token,
                         user=UserOut.model_validate(user))


@auth_router.post("/login", response_model=TokenResponse)
async def login(form: OAuth2PasswordRequestForm = Depends(), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == form.username))
    user   = result.scalar_one_or_none()
    if not user or not verify_password(form.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token  = create_access_token(user.id)
    refresh_token = await create_refresh_token(user.id, db)
    return TokenResponse(access_token=access_token, refresh_token=refresh_token,
                         user=UserOut.model_validate(user))


@auth_router.post("/refresh", response_model=TokenResponse)
async def refresh(body: RefreshRequest, db: AsyncSession = Depends(get_db)):
    """Exchange a valid refresh token for a new access + refresh token pair."""
    token_hash = _hash_token(body.refresh_token)
    result     = await db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    )
    stored = result.scalar_one_or_none()

    now = datetime.now(timezone.utc)
    if (
        not stored
        or stored.revoked
        or stored.expires_at.replace(tzinfo=timezone.utc) < now
    ):
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")

    # Rotate: revoke old token, issue new pair
    stored.revoked = True
    await db.flush()

    user_result = await db.execute(select(User).where(User.id == stored.user_id))
    user        = user_result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")

    access_token      = create_access_token(user.id)
    new_refresh_token = await create_refresh_token(user.id, db)
    return TokenResponse(access_token=access_token, refresh_token=new_refresh_token,
                         user=UserOut.model_validate(user))


@auth_router.post("/forgot-password", status_code=200)
async def forgot_password(body: ForgotPasswordRequest, db: AsyncSession = Depends(get_db)):
    """Always returns 200 to avoid email enumeration."""
    from email_service import send_password_reset_email  # local import avoids circular dep
    import asyncio

    result = await db.execute(select(User).where(User.email == body.email))
    user   = result.scalar_one_or_none()
    if not user:
        return {"message": "If that email exists, a reset link has been sent."}

    raw        = secrets.token_urlsafe(32)
    token_hash = _hash_token(raw)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=RESET_TOKEN_EXPIRE_MINUTES)

    db.add(PasswordResetToken(user_id=user.id, token_hash=token_hash, expires_at=expires_at))
    await db.commit()

    # Fire-and-forget — don't block the response on email delivery
    asyncio.create_task(
        asyncio.to_thread(send_password_reset_email, user.email, raw)
    )
    return {"message": "If that email exists, a reset link has been sent."}


@auth_router.post("/reset-password", status_code=200)
async def reset_password(body: ResetPasswordRequest, db: AsyncSession = Depends(get_db)):
    if len(body.new_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    if len(body.new_password) > 72:
        raise HTTPException(status_code=400, detail="Password must be under 72 characters")

    token_hash = _hash_token(body.token)
    result     = await db.execute(
        select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash)
    )
    stored = result.scalar_one_or_none()
    now    = datetime.now(timezone.utc)

    if (
        not stored
        or stored.used
        or stored.expires_at.replace(tzinfo=timezone.utc) < now
    ):
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")

    user_result = await db.execute(select(User).where(User.id == stored.user_id))
    user        = user_result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=400, detail="User not found")

    user.hashed_password = hash_password(body.new_password)
    stored.used          = True

    # Revoke all existing refresh tokens on password reset
    tokens_result = await db.execute(
        select(RefreshToken).where(RefreshToken.user_id == user.id, RefreshToken.revoked == False)
    )
    for t in tokens_result.scalars().all():
        t.revoked = True

    await db.commit()
    return {"message": "Password reset successful"}


@auth_router.post("/logout", status_code=200)
async def logout(body: RefreshRequest, db: AsyncSession = Depends(get_db)):
    """Revoke the refresh token on explicit logout."""
    token_hash = _hash_token(body.refresh_token)
    result     = await db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    )
    stored = result.scalar_one_or_none()
    if stored:
        stored.revoked = True
        await db.commit()
    return {"message": "Logged out"}


@auth_router.get("/me", response_model=UserOut)
async def me(current_user: User = Depends(require_active_user)):
    return current_user