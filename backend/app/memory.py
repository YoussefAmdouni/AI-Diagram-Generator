from datetime import datetime

class ConversationMemory:
    """Conversation memory manager."""
    
    def __init__(self, thread_id: str):
        self.thread_id = thread_id
        self.created_at = datetime.now()
        self.conversation_history = []
        self.action_history = []
    
    def add_user_message(self, content: str):
        """Add user message to memory."""
        self.conversation_history.append({
            "type": "user",
            "content": content,
            "timestamp": datetime.now().isoformat()
        })
    
    def add_assistant_message(self, content: str):
        """Add assistant message to memory."""
        self.conversation_history.append({
            "type": "assistant",
            "content": content,
            "timestamp": datetime.now().isoformat()
        })
    
    def add_action(self, tool_name: str, tool_input: dict, result: str):
        """Track tool actions and their results."""
        self.action_history.append({
            "tool": tool_name,
            "input": tool_input,
            "result": result,
            "timestamp": datetime.now().isoformat()
        })
    
    def get_summary(self, max_messages: int = 5) -> str:
        """Get a summary of recent conversation."""
        recent = self.conversation_history[-max_messages:] if self.conversation_history else []
        summary = "\n".join([f"{msg['type'].upper()}: {msg['content']}" for msg in recent])
        return summary or "No conversation yet"
    
    def to_dict(self) -> dict:
        """Export memory state."""
        return {
            "thread_id": self.thread_id,
            "created_at": self.created_at.isoformat(),
            "conversation_history": self.conversation_history,
            "action_history": self.action_history
        }