"""
Database setup using SQLAlchemy async with SQLite.
Switch to Postgres by changing DATABASE_URL to:
  postgresql+asyncpg://user:password@localhost/dbname
"""
import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, relationship
from sqlalchemy import Column, String, DateTime, ForeignKey, Text, Boolean, func
import uuid
from datetime import datetime

# ─── Connection ────────────────────────────────────────────────────────────────
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./mermaid_app.db")

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    **({} if "postgresql" in DATABASE_URL else {"connect_args": {"check_same_thread": False}}),
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,   # Keep objects usable after commit without re-querying
)


# ─── Base ──────────────────────────────────────────────────────────────────────
class Base(DeclarativeBase):
    pass


# ─── Models ───────────────────────────────────────────────────────────────────
class User(Base):
    __tablename__ = "users"

    id               = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    email            = Column(String, unique=True, nullable=False, index=True)
    hashed_password  = Column(String, nullable=False)
    is_active        = Column(Boolean, default=True)
    created_at       = Column(DateTime, default=datetime.utcnow)

    # lazy="noload" — never auto-fetch; always query explicitly in endpoints
    conversations = relationship(
        "Conversation", back_populates="user",
        cascade="all, delete-orphan", lazy="noload"
    )


class Conversation(Base):
    __tablename__ = "conversations"

    id         = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id    = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    title      = Column(String, default="New Conversation")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)   # set explicitly in endpoints

    user = relationship("User", back_populates="conversations", lazy="noload")
    # lazy="noload" — message_count is always computed via SQL COUNT, never lazy loaded
    messages = relationship(
        "Message", back_populates="conversation",
        cascade="all, delete-orphan", lazy="noload",
        order_by="Message.created_at"
    )


class Message(Base):
    __tablename__ = "messages"

    id              = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    conversation_id = Column(String, ForeignKey("conversations.id", ondelete="CASCADE"),
                             nullable=False, index=True)
    role            = Column(String, nullable=False)   # "user" | "assistant"
    content         = Column(Text, nullable=False)
    created_at      = Column(DateTime, default=datetime.utcnow)

    conversation = relationship("Conversation", back_populates="messages", lazy="noload")


# ─── Helpers ──────────────────────────────────────────────────────────────────
async def create_tables():
    """Create all tables on startup."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db():
    """FastAPI dependency — yields an async DB session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
