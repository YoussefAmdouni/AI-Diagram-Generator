from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from agent import get_response, get_conversation_messages, logger
from typing import Optional
from datetime import datetime
from pathlib import Path
import json
import uuid
import threading
from threading import Lock
import asyncio
import time
import os
from memory import ConversationMemory

from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

# Session management with thread-safe access
class SessionManager:
    """Manages user sessions with persistent memory using threads."""
    
    def __init__(self):
        self.sessions = {}  # session_id -> {memory, thread, lock}
        self.conversations = {}  # conversation_id -> {title, created_at, updated_at, message_count, session_id}
        self.global_lock = Lock()
        self.sessions_file = Path("sessions.json")
    
    def create_session(self, conversation_id: str, title: str = "New Conversation") -> str:
        """Create a new session with persistent memory."""
        with self.global_lock:
            session_id = str(uuid.uuid4())
            memory = ConversationMemory(conversation_id)
            
            self.sessions[session_id] = {
                "session_id": session_id,
                "conversation_id": conversation_id,
                "memory": memory,
                "lock": Lock(),
                "created_at": datetime.now().isoformat(),
                "thread_id": threading.current_thread().ident
            }
            
            # Store conversation metadata
            self.conversations[conversation_id] = {
                "id": conversation_id,
                "session_id": session_id,
                "title": title,
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
                "message_count": 0
            }
            
            self._save_to_file()
            return session_id
    
    def get_session(self, session_id: str) -> Optional[dict]:
        """Get a session's memory safely."""
        with self.global_lock:
            return self.sessions.get(session_id)
    
    def get_conversation(self, conversation_id: str) -> Optional[dict]:
        """Get conversation metadata."""
        with self.global_lock:
            return self.conversations.get(conversation_id)
    
    def get_all_conversations(self) -> list:
        """Get all conversations sorted by updated_at."""
        with self.global_lock:
            conv_list = list(self.conversations.values())
            conv_list.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
            return conv_list
    
    def add_user_message(self, session_id: str, message: str):
        """Add user message to session memory."""
        session = self.get_session(session_id)
        if session:
            with session["lock"]:
                session["memory"].add_user_message(message)
    
    def add_assistant_message(self, session_id: str, message: str):
        """Add assistant message to session memory."""
        session = self.get_session(session_id)
        if session:
            with session["lock"]:
                session["memory"].add_assistant_message(message)
    
    def get_conversation_history(self, session_id: str) -> list:
        """Get conversation history for a session."""
        session = self.get_session(session_id)
        if session:
            with session["lock"]:
                return session["memory"].conversation_history.copy()
        return []
    
    def update_conversation_metadata(self, conversation_id: str, title: Optional[str] = None):
        """Update conversation metadata (timestamp, title, message count)."""
        with self.global_lock:
            if conversation_id in self.conversations:
                self.conversations[conversation_id]["updated_at"] = datetime.now().isoformat()
                self.conversations[conversation_id]["message_count"] = self.conversations[conversation_id].get("message_count", 0) + 1
                
                if title and self.conversations[conversation_id]["title"] == "New Conversation":
                    self.conversations[conversation_id]["title"] = title
                
                self._save_to_file()
    
    def delete_session(self, session_id: str):
        """Delete a session and its conversation."""
        with self.global_lock:
            if session_id in self.sessions:
                conversation_id = self.sessions[session_id]["conversation_id"]
                del self.sessions[session_id]
                
                if conversation_id in self.conversations:
                    del self.conversations[conversation_id]
                
                self._save_to_file()
    
    def delete_conversation(self, conversation_id: str):
        """Delete a conversation and its session."""
        with self.global_lock:
            # Find and delete associated session
            session_id_to_delete = None
            for sid, session in self.sessions.items():
                if session["conversation_id"] == conversation_id:
                    session_id_to_delete = sid
                    break
            
            if session_id_to_delete:
                del self.sessions[session_id_to_delete]
            
            if conversation_id in self.conversations:
                del self.conversations[conversation_id]
            
            self._save_to_file()
    
    def _save_to_file(self):
        """Save all sessions and conversations to local file (thread-safe with file locking)."""
        sessions_data = {}
        conversations_data = {}
        
        for session_id, session in self.sessions.items():
            sessions_data[session_id] = {
                "session_id": session["session_id"],
                "conversation_id": session["conversation_id"],
                "created_at": session["created_at"],
                "thread_id": session["thread_id"],
                "memory": session["memory"].to_dict()
            }
        
        for conv_id, conv in self.conversations.items():
            conversations_data[conv_id] = conv
        
        # Use file locking to ensure safe concurrent writes
        lock_file = self.sessions_file.with_suffix('.lock')
        max_wait = 5  # Max 5 seconds to acquire lock
        start_time = time.time()
        
        while True:
            try:
                # Try to create lock file exclusively
                if not lock_file.exists():
                    lock_file.touch(exist_ok=True)
                    break
                elif time.time() - start_time > max_wait:
                    logger.warning("Could not acquire file lock, proceeding anyway")
                    break
                else:
                    time.sleep(0.01)  # Wait 10ms before retrying
            except Exception as e:
                logger.warning(f"Lock file creation issue: {e}")
                break
        
        try:
            # Write to temporary file first
            temp_file = self.sessions_file.with_suffix('.tmp')
            data = {
                "sessions": sessions_data,
                "conversations": conversations_data
            }
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            
            # Atomic rename
            if temp_file.exists():
                if self.sessions_file.exists():
                    self.sessions_file.unlink()
                temp_file.rename(self.sessions_file)
                
        except Exception as e:
            logger.error(f"Could not save sessions to file: {e}")
        finally:
            # Release lock
            try:
                if lock_file.exists():
                    lock_file.unlink()
            except Exception as e:
                logger.debug(f"Could not remove lock file: {e}")
    
    def load_sessions(self):
        """Load sessions and conversations from local storage file (call on startup)."""
        sessions_data = {}
        conversations_data = {}
        
        if self.sessions_file.exists():
            # Use file locking to ensure safe reads
            lock_file = self.sessions_file.with_suffix('.lock')
            max_wait = 5  # Max 5 seconds to acquire lock
            start_time = time.time()
            
            while True:
                try:
                    # Wait for any writes to complete
                    if not lock_file.exists():
                        break
                    elif time.time() - start_time > max_wait:
                        logger.warning("Could not acquire file lock for reading, proceeding anyway")
                        break
                    else:
                        time.sleep(0.01)  # Wait 10ms before retrying
                except Exception as e:
                    logger.debug(f"Lock file check issue: {e}")
                    break
            
            try:
                with open(self.sessions_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    sessions_data = data.get("sessions", {})
                    conversations_data = data.get("conversations", {})
                    logger.info(f"Loaded {len(sessions_data)} sessions and {len(conversations_data)} conversations from local storage")
            except Exception as e:
                logger.error(f"Could not load sessions from file: {e}")
                sessions_data = {}
                conversations_data = {}
        
        self._populate_from_data(sessions_data, conversations_data)
    
    def _populate_from_data(self, sessions_data: dict, conversations_data: dict):
        """Helper to populate sessions and conversations from loaded data."""
        # Load conversations first (they contain metadata)
        self.conversations = conversations_data
        
        # Load sessions with memory
        for session_id, data in sessions_data.items():
            memory = ConversationMemory(data["conversation_id"])
            memory.conversation_history = data["memory"].get("conversation_history", [])
            memory.action_history = data["memory"].get("action_history", [])
            
            self.sessions[session_id] = {
                "session_id": session_id,
                "conversation_id": data["conversation_id"],
                "memory": memory,
                "lock": Lock(),
                "created_at": data["created_at"],
                "thread_id": data.get("thread_id")
            }

session_manager = SessionManager()
session_manager.load_sessions()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class Prompt(BaseModel):
    message: str
    conversation_id: Optional[str] = None
    session_id: Optional[str] = None

class ConversationCreate(BaseModel):
    title: Optional[str] = "New Conversation"

@app.post("/api/sessions")
async def create_session(conv: ConversationCreate):
    """Create a new session with conversation."""
    try:
        conversation_id = str(uuid.uuid4())
        
        # Create session with conversation metadata (all in one)
        session_id = session_manager.create_session(conversation_id, conv.title)
        
        logger.info(f"Created session {session_id} for conversation {conversation_id}")
        
        return {
            "conversation_id": conversation_id,
            "session_id": session_id,
            "title": conv.title,
            "created_at": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"Error creating session: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/prompt")
async def handle_prompt(prompt: Prompt):
    """Handle a chat message within a session."""
    session_id = prompt.session_id
    conversation_id = prompt.conversation_id
    
    # If session_id is provided, use it directly
    if session_id:
        session = session_manager.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        conversation_id = session["conversation_id"]
    # Otherwise, if conversation_id is provided, find or create session for it
    elif conversation_id:
        # Find existing session for this conversation
        for sid, sess in session_manager.sessions.items():
            if sess["conversation_id"] == conversation_id:
                session_id = sid
                break
        
        # If no session exists, create one
        if not session_id:
            session_id = session_manager.create_session(conversation_id)
            logger.info(f"Created session {session_id} for conversation {conversation_id}")
    else:
        raise HTTPException(status_code=400, detail="session_id or conversation_id is required")
    
    # Get session
    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    conversation_id = session["conversation_id"]
    
    # Add user message to memory
    session_manager.add_user_message(session_id, prompt.message)
    
    # Log user input
    logger.info(f"[Session: {session_id}] [Conversation: {conversation_id}] USER INPUT: {prompt.message}")
    
    # Get conversation history for context
    conversation_history = session_manager.get_conversation_history(session_id)
    
    # Get response with persistent conversation ID and prior context
    response = await get_response(prompt.message, conversation_id, conversation_history)
    
    # Add assistant message to memory
    session_manager.add_assistant_message(session_id, response)
    
    # Update conversation metadata (title for first message, message count, timestamp)
    title = None
    if prompt.message:
        title = prompt.message[:50] + ("..." if len(prompt.message) > 50 else "")
    session_manager.update_conversation_metadata(conversation_id, title)
    
    # Log bot response
    logger.info(f"[Session: {session_id}] BOT RESPONSE: {response}")
    logger.info("-" * 80)
    
    return {"message": response, "session_id": session_id}

@app.get("/api/conversations")
async def list_conversations():
    """List all conversations."""
    conv_list = session_manager.get_all_conversations()
    return {"conversations": conv_list}

@app.post("/api/conversations")
async def create_conversation(conv: ConversationCreate):
    """Create a new conversation with session."""
    conversation_id = str(uuid.uuid4())
    
    # Create session with conversation metadata (all in one)
    session_id = session_manager.create_session(conversation_id, conv.title)
    
    task_conv = session_manager.get_conversation(conversation_id)
    logger.info(f"Created conversation {conversation_id} with session {session_id}")
    
    return {
        "id": conversation_id,
        "session_id": session_id,
        **task_conv
    }

@app.delete("/api/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str):
    """Delete a conversation."""
    conv = session_manager.get_conversation(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    
    session_manager.delete_conversation(conversation_id)
    return {"message": "Conversation deleted"}

@app.get("/api/conversations/{conversation_id}/messages")
async def get_messages(conversation_id: str, session_id: Optional[str] = None):
    """Get all messages from a conversation/session."""
    conv = session_manager.get_conversation(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    
    # Always use session memory if session_id provided
    if session_id:
        history = session_manager.get_conversation_history(session_id)
        return {"messages": history}
    
    # If no session_id, find the session for this conversation
    for sid, session in session_manager.sessions.items():
        if session["conversation_id"] == conversation_id:
            history = session_manager.get_conversation_history(sid)
            return {"messages": history}
    
    # No session found - return empty messages
    return {"messages": []}

@app.get("/api/sessions/{session_id}")
async def get_session_info(session_id: str):
    """Get session information and memory."""
    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    return {
        "session_id": session_id,
        "conversation_id": session["conversation_id"],
        "created_at": session["created_at"],
        "thread_id": session["thread_id"],
        "message_count": len(session["memory"].conversation_history)
    }

@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str):
    """Delete a session."""
    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    session_manager.delete_session(session_id)
    return {"message": "Session deleted"}

if __name__ == "__main__":
    import uvicorn
    
    # Mount static files from frontend
    frontend_path = Path(__file__).parent.parent / "frontend"
    if frontend_path.exists():
        app.mount("/", StaticFiles(directory=str(frontend_path), html=True), name="frontend")
    
    uvicorn.run(app, host="0.0.0.0", port=8000)
