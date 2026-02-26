"""
Semi-prod FastAPI backend.
"""
import os
import uuid
from context import request_id_var
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload

from agent import get_response, logger
from auth import auth_router, require_active_user
from database import create_tables, get_db, User, Conversation, Message

from dotenv import load_dotenv
load_dotenv()

# ─── Rate limiter ──────────────────────────────────────────────────────────────
limiter = Limiter(key_func=get_remote_address, default_limits=["20/minute"])


# ─── Lifespan ──────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    await create_tables()
    logger.info("Database tables ready")
    yield


app = FastAPI(title="Mermaid AI Assistant", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ─── CORS ──────────────────────────────────────────────────────────────────────
DEV_MODE = os.getenv("DEV_MODE", "true").lower() == "true"

if DEV_MODE:
    logger.info("CORS: DEV_MODE=true — allowing all origins")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
else:
    ALLOWED_ORIGINS = [o.strip() for o in os.getenv(
        "ALLOWED_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000"
    ).split(",")]
    logger.info(f"CORS: production — allowed origins: {ALLOWED_ORIGINS}")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Accept"],
        max_age=600,
    )


# ─── Request ID middleware ─────────────────────────────────────────────────────
@app.middleware("http")
async def attach_request_id(request: Request, call_next):
    req_id = str(uuid.uuid4())[:8]
    request_id_var.set(req_id)
    request.state.request_id = req_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = req_id
    return response

# ─── Auth routes ───────────────────────────────────────────────────────────────
app.include_router(auth_router)


# ─── Schemas ───────────────────────────────────────────────────────────────────
class ConversationCreate(BaseModel):
    title: Optional[str] = "New Conversation"

class PromptRequest(BaseModel):
    message: str
    conversation_id: str

class PromptResponse(BaseModel):
    message: str
    conversation_id: str


# ─── Health ────────────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


# ─── Conversations ─────────────────────────────────────────────────────────────
@app.get("/api/conversations")
@limiter.limit("60/minute")
async def list_conversations(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_active_user),
):
    # Use a subquery to count messages without lazy loading
    msg_count_subq = (
        select(Message.conversation_id, func.count(Message.id).label("cnt"))
        .group_by(Message.conversation_id)
        .subquery()
    )

    result = await db.execute(
        select(Conversation, func.coalesce(msg_count_subq.c.cnt, 0).label("message_count"))
        .outerjoin(msg_count_subq, Conversation.id == msg_count_subq.c.conversation_id)
        .where(Conversation.user_id == current_user.id)
        .order_by(Conversation.updated_at.desc())
    )
    rows = result.all()

    return {
        "conversations": [
            {
                "id": conv.id,
                "title": conv.title,
                "created_at": conv.created_at,
                "updated_at": conv.updated_at,
                "message_count": count,
            }
            for conv, count in rows
        ]
    }


@app.post("/api/conversations", status_code=201)
@limiter.limit("20/minute")
async def create_conversation(
    request: Request,
    body: ConversationCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_active_user),
):
    conv = Conversation(
        id=str(uuid.uuid4()),
        user_id=current_user.id,
        title=body.title or "New Conversation",
        updated_at=datetime.utcnow(),
    )
    db.add(conv)
    await db.commit()
    await db.refresh(conv)
    logger.info(f"[{current_user.email}] Created conversation {conv.id}")
    return {
        "id": conv.id,
        "title": conv.title,
        "created_at": conv.created_at,
        "updated_at": conv.updated_at,
        "message_count": 0,
    }


@app.delete("/api/conversations/{conversation_id}")
@limiter.limit("20/minute")
async def delete_conversation(
    request: Request,
    conversation_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_active_user),
):
    result = await db.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == current_user.id,
        )
    )
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    await db.delete(conv)
    await db.commit()
    return {"message": "Conversation deleted"}


@app.get("/api/conversations/{conversation_id}/messages")
@limiter.limit("60/minute")
async def get_messages(
    request: Request,
    conversation_id: str,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_active_user),
):
    # Ownership check
    conv_result = await db.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == current_user.id,
        )
    )
    if not conv_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Conversation not found")

    msg_result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc())   # oldest first for chat display
        .limit(limit)
    )
    messages = msg_result.scalars().all()
    return {
        "messages": [
            {"id": m.id, "role": m.role, "content": m.content, "created_at": m.created_at}
            for m in messages
        ]
    }


# ─── Prompt ────────────────────────────────────────────────────────────────────
@app.post("/api/prompt", response_model=PromptResponse)
@limiter.limit("20/minute")
async def handle_prompt(
    request: Request,
    body: PromptRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_active_user),
):
    conv_result = await db.execute(
        select(Conversation).where(
            Conversation.id == body.conversation_id,
            Conversation.user_id == current_user.id,
        )
    )
    conv = conv_result.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    user_message = body.message.strip()
    if not user_message:
        raise HTTPException(status_code=400, detail="Message cannot be empty")
    if len(user_message) > 8000:
        raise HTTPException(status_code=400, detail="Message too long (max 8000 chars)")

    # Persist user message
    db.add(Message(conversation_id=conv.id, role="user", content=user_message))
    await db.flush()

    # Build context from last 10 messages (explicit async query, no lazy load)
    history_result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conv.id)
        .order_by(Message.created_at.desc())
        .limit(10)
    )
    context = [
        {"type": m.role, "content": m.content}
        for m in reversed(history_result.scalars().all())
    ]

    logger.info(f"[{current_user.email}][{conv.id}] USER: {user_message[:80]}")
    response_text = await get_response(user_message, context)

    # Persist assistant message
    db.add(Message(conversation_id=conv.id, role="assistant", content=response_text))

    # Update title on first real message
    if conv.title == "New Conversation":
        conv.title = user_message[:50] + ("..." if len(user_message) > 50 else "")

    # Explicitly set updated_at so sidebar ordering works
    conv.updated_at = datetime.utcnow()

    await db.commit()
    logger.info(f"[{current_user.email}][{conv.id}] BOT: {response_text[:80]}")
    return PromptResponse(message=response_text, conversation_id=conv.id)


# ─── Static frontend ───────────────────────────────────────────────────────────
frontend_path = Path(__file__).parent.parent.parent / "frontend"
if frontend_path.exists():
    app.mount("/", StaticFiles(directory=str(frontend_path), html=True), name="frontend")
    logger.info(f"Serving frontend from {frontend_path}")
else:
    logger.warning(f"Frontend not found at {frontend_path} — serve it separately")


# ─── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
