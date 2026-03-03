"""
Database setup — default to Neon (Postgres). SQLite fallback for local dev.
"""
import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, relationship
from sqlalchemy import Column, String, DateTime, ForeignKey, Text, Boolean
import uuid
from datetime import datetime, timezone

DATABASE_URL = os.getenv("DATABASE_URL", "")

# Strip query params — pass SSL cleanly via connect_args for asyncpg
_clean_url = DATABASE_URL.split("?")[0]

engine = create_async_engine(
    _clean_url,
    echo=False,
    connect_args={"ssl": "require"},
)

AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"
    id              = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    email           = Column(String, unique=True, nullable=False, index=True)
    hashed_password = Column(String, nullable=False)
    is_active       = Column(Boolean, default=True)
    created_at      = Column(DateTime(timezone=True), default=_now)

    conversations  = relationship("Conversation", back_populates="user", cascade="all, delete-orphan", lazy="noload")
    refresh_tokens = relationship("RefreshToken",  back_populates="user", cascade="all, delete-orphan", lazy="noload")
    reset_tokens   = relationship("PasswordResetToken", back_populates="user", cascade="all, delete-orphan", lazy="noload")


class RefreshToken(Base):
    """Long-lived, revocable token stored server-side."""
    __tablename__ = "refresh_tokens"
    id         = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id    = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash = Column(String, nullable=False, unique=True)  # SHA-256 of raw token
    expires_at = Column(DateTime(timezone=True), nullable=False)
    revoked    = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=_now)
    user = relationship("User", back_populates="refresh_tokens", lazy="noload")


class PasswordResetToken(Base):
    """Single-use token for password reset."""
    __tablename__ = "password_reset_tokens"
    id         = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id    = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash = Column(String, nullable=False, unique=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used       = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=_now)
    user = relationship("User", back_populates="reset_tokens", lazy="noload")


class Conversation(Base):
    __tablename__ = "conversations"
    id         = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id    = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    title      = Column(String, default="New Conversation")
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now)
    user     = relationship("User", back_populates="conversations", lazy="noload")
    messages = relationship("Message", back_populates="conversation", cascade="all, delete-orphan",
                            lazy="noload", order_by="Message.created_at")


class Message(Base):
    __tablename__ = "messages"
    id              = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    conversation_id = Column(String, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True)
    role            = Column(String, nullable=False)
    content         = Column(Text, nullable=False)
    created_at      = Column(DateTime(timezone=True), default=_now)
    conversation = relationship("Conversation", back_populates="messages", lazy="noload")


async def create_tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()