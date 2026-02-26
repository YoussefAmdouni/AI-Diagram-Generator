# 🎨 Simple Mermaid APP - AI Designer Assistant

An intelligent web application that generates Mermaid diagrams from natural language descriptions using AI. Built with FastAPI backend, an agentic workflow system, and a React-like frontend.

**Status**: Real-time diagram generation, conversation management, and loading indicators.

---

## 📋 Table of Contents

- [Features](#features)
- [Architecture](#architecture)
- [Tech Stack](#tech-stack)
- [Project Structure](#project-structure)
- [Setup & Installation](#setup--installation)
- [Usage](#usage)
- [API Endpoints](#api-endpoints)

---

## ✨ Features

### Core Functionality
- **AI-Powered Diagram Generation** - Converts natural language to Mermaid code using Google's Gemini model
- **Multi-turn Conversations** - Maintain separate conversation threads with persistent memory
- **Intelligent Routing** - Routes queries to appropriate handlers (workflow, direct, or refuses unsafe requests)
- **Syntax Validation** - Validates generated Mermaid syntax before rendering
- **Tool Integration** - Uses web search and mermaid syntax checking tools
- **Diagram Rendering** - Real-time rendering with error handling
- **Conversation Persistence** - Saves all conversations and session states to local storage

### UX/UI Features
- **Loading Indicator** - Animated spinner with "Model is thinking..." text
- **Agent Steps Display** - Shows real-time agent workflow steps
- **Conversation History** - Sidebar with all conversations, sortable by date
- **Message Persistence** - Load previous messages when switching conversations
- **Copy & Download** - Copy Mermaid code or download diagrams as PNG
- **Responsive Design** - Works on desktop and mobile devices
- **Professional UI** - Modern dark theme with smooth animations

---

## Architecture

```
┌─────────────────────────────────────────┐
│         Frontend (index.html)            │
│  - Chat interface with Mermaid renderer  │
│  - Conversation sidebar                  │
│  - Loading indicator & step display      │
└──────────────┬──────────────────────────┘
               │ HTTP/REST
┌──────────────▼──────────────────────────┐
│    FastAPI Backend (main.py)             │
│  - Session & Conversation Management     │
│  - Thread-safe file operations           │
│  - RESTful API endpoints                 │
└──────────────┬──────────────────────────┘
               │
┌──────────────▼──────────────────────────┐
│     Agent Workflow (agent.py)            │
│  - LangGraph state machine               │
│  - Orchestrator node (routing)           │
│  - Direct node (web search)              │
│  - Mermaid node (diagram generation)     │
└──────────────┬──────────────────────────┘
               │
┌──────────────▴──────────────────────────┐
│    LLM & Tools (Gemini API)              │
│  - Web search tool (Tavily)              │
│  - Mermaid syntax validator              │
└─────────────────────────────────────────┘
```

### Data Flow

1. **User Input** → Frontend sends message to backend
2. **Session Management** → Backend retrieves or creates session with conversation history
3. **Agent Routing** → Orchestrator decides: workflow, direct, or unsafe
4. **Workflow**: Mermaid node iterates with LLM until valid diagram generated
5. **Direct**: LLM answers with optional web search
6. **Response** → Formatted and sent to frontend
7. **Rendering** → Frontend renders Mermaid diagram with animations
8. **Persistence** → Conversation saved to `sessions.json`

---

## Tech Stack

### Backend
- **Framework**: FastAPI
- **AI/LLM**: LangGraph + Google Gemini API
- **Language Models**: 
  - `gemini-2.5-flash` - Main model (temperature: 0.0)
  - `gemini-2.5-flash-lite` - Alternative (lighter)
- **Tools**: 
  - Tavily Search (web search)
  - Mermaid CLI (syntax validation)
- **Storage**: JSON files (sessions.json)
- **Concurrency**: Threading with locks for thread-safe operations

### Frontend
- **HTML5** - Semantic markup
- **CSS3** - Modern styling with animations
- **Vanilla JavaScript** - No frameworks (pure ES6+)
- **Libraries**:
  - Mermaid.js - Diagram rendering
  - Font Awesome - Icons
  - dom-to-image - PNG export
  - Google Fonts - Typography

### Development
- **Python 3.12**
- **uvicorn** - ASGI server
- **python-dotenv** - Environment variables

---

## 📁 Project Structure

```
Simple Mermaid APP/
├── README.md                           # Project documentation
│
├── backend/
│   ├── requirements.txt
│   └── app/
│       ├── main.py                    # FastAPI application
│       ├── agent.py                   # LangGraph agent workflow
│       ├── memory.py                  # Conversation memory manager
│       ├── tool.py                    # Custom tools (web search, mermaid check)
│       ├── prompt.yaml                # LLM prompts configuration
│       ├── sessions.json              # Persistent session storage
│       │                              # Format: {sessions: {...}, conversations: {...}}
│       └── agent_logs/               # Timestamped agent execution logs
│
└── frontend/
    ├── index.html                     # Main UI
    ├── style.css                      # Responsive styling
    └── script.js                      # Client-side logic
```

---

## Setup & Installation

### Prerequisites
- Python 3.10+
- Node.js (optional, for frontend dev tools)
- Mermaid CLI: `npm install -g @mermaid-js/mermaid-cli`
- Google Gemini API key
- Tavily API key (for web search)

### 1. Clone & Navigate
```bash
cd "Simple Mermaid APP"
```

### 2. Create Python Environment
```bash
python -m venv venv
venv\Scripts\activate  # Windows
# or
source venv/bin/activate  # macOS/Linux
```

### 3. Install Backend Dependencies
```bash
cd backend
pip install -r requirements.txt
```

### 4. Configure Environment
Create `.env` file in `backend/app/`:
```env
GOOGLE_API_KEY=your_gemini_api_key_here
TAVILY_API_KEY=your_tavily_api_key_here
```

### 5. Start Backend Server
```bash
cd backend/app
python main.py
```

Server runs at: `http://localhost:8000`

### 6. Open Frontend
Navigate to: `http://localhost:8000` in your browser

---

## Usage

### Basic Workflow

1. **Create New Chat**: Click "+" button in sidebar
2. **Enter Prompt**: Type your diagram description
   - Example: "Create a flowchart for user authentication process"
3. **Wait for Response**: Watch loading indicator with "Model is thinking..."
4. **View Diagram**: Rendered Mermaid diagram appears in chat
5. **Actions**:
   - **Copy**: Click copy icon to copy Mermaid code
   - **Download**: Click download icon to save as PNG
   - **Continue**: Ask follow-up questions in same conversation

### Advanced Features

**Switch Conversations**: Click any conversation in sidebar
- Loads all previous messages
- Resets rendering engine to prevent conflicts

**Edit Query**: Edit your prompt in the query (currently supports new messages)

**Delete Conversation**: Click trash icon to remove conversation

---

## API Endpoints

### Conversations
```
GET  /api/conversations
     - Returns all conversations (sorted by newest first)
     - Response: {conversations: [...]}

POST /api/conversations
     - Creates new conversation
     - Body: {title: string}
     - Response: {id, session_id, title, created_at, updated_at, message_count}

DELETE /api/conversations/{conversation_id}
     - Deletes conversation and associated session
     - Response: {message: "Conversation deleted"}

GET /api/conversations/{conversation_id}/messages
     - Fetches all messages in conversation
     - Query params: ?session_id=<id> (optional)
     - Response: {messages: [...]}
```

### Prompts & Sessions
```
POST /api/prompt
     - Sends user message and gets AI response
     - Body: {
       message: string,
       conversation_id: string,
       session_id: string (optional)
     }
     - Response: {message: string, session_id: string}

POST /api/sessions
     - Creates new session (legacy endpoint)
     - Body: {title: string}

GET  /api/sessions/{session_id}
     - Gets session info
     - Response: {session_id, conversation_id, created_at, thread_id, message_count}

DELETE /api/sessions/{session_id}
     - Deletes session and conversation
     - Response: {message: "Session deleted"}
```

---

## Configuration

### LLM Settings (agent.py)
```python
llm = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash",  # Fast model
    temperature=0.0             # Deterministic output
)
```

### Session Persistence
- **Storage**: `sessions.json` (unified format)
- **Atomic Writes**: Uses temporary files + rename
- **File Locking**: Prevents concurrent write conflicts
- **Auto-recovery**: Loads sessions on startup

### Mermaid Configuration
```javascript
mermaid.initialize({ 
    startOnLoad: false,
    theme: 'default',
    securityLevel: 'loose'
});
```

### CORS Settings
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

---