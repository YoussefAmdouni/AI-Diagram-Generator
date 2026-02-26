# 🎨 AI Diagram Generator - Mermaid Assistant

Generate Mermaid diagrams from natural language using AI. Built with FastAPI, LangGraph, Google Gemini, and Vanilla JS.

---

## ✨ Features

- **AI-Powered Diagram Generation** - Google Gemini converts natural language to Mermaid code
- **Multi-turn Conversations** - Persistent conversation threads with message history
- **User Authentication** - JWT-based auth with bcrypt password hashing
- **Real-time Rendering** - Instant Mermaid diagram preview
- **Persistent Storage** - SQLite/PostgreSQL database for conversations and users
- **Web Search** - Tavily integration for research-aware responses
- **Rate Limiting** - Built-in protection against abuse
- **Responsive UI** - Modern dark theme, works on desktop & mobile

---

## 🏗️ Architecture

```
Frontend (Vanilla JS) ← HTTP/REST → FastAPI Backend
                                         ↓
                                    Database (SQLAlchemy)
                                         ↓
                                    LangGraph Agent
                                         ↓
                                    Google Gemini API
```

---

## 🛠️ Tech Stack

**Backend**: FastAPI, SQLAlchemy, LangGraph, Google Gemini, Tavily Search, JWT Auth, Rate Limiting  
**Frontend**: HTML5, CSS3, Vanilla JavaScript, Mermaid.js, Font Awesome  
**Database**: SQLite (dev) / PostgreSQL (production)  
**Python 3.10+**

---

## 📁 Project Structure

```
backend/app/
├── main.py              # FastAPI routes
├── agent.py             # LangGraph workflow
├── auth.py              # Authentication
├── database.py          # SQLAlchemy models
├── tool.py              # Custom tools
├── prompt.yaml          # LLM prompts
├── mermaid_app.db       # SQLite database
└── agent_logs/          # Execution logs

frontend/
├── index.html           # UI + Auth Modal
├── script.js            # Client logic
├── style.css            # Main styling
└── auth.css             # Auth styling
```

---

## 🚀 Quick Start

### 1. Setup
```bash
cd "AI Diagram Generator"
python -m venv venv
venv\Scripts\activate   # Windows
cd backend
pip install -r requirements.txt
```

### 2. Configure `.env` in `backend/app/`
```env
GOOGLE_API_KEY=your_key
TAVILY_API_KEY=your_key
SECRET_KEY=your_secret_key
ACCESS_TOKEN_EXPIRE_MINUTES=1440
DEV_MODE=true
DATABASE_URL=sqlite+aiosqlite:///./mermaid_app.db
``` 

### 3. Run
```bash
cd backend/app
python main.py
```

Open `http://localhost:8000` → Sign up → Start creating diagrams!

---

## 📡 API Endpoints

### Auth
- `POST /api/auth/register` - Create account
- `POST /api/auth/login` - Login
- `GET /api/auth/me` - Current user

### Conversations (Auth required)
- `GET /api/conversations` - List all
- `POST /api/conversations` - Create new
- `DELETE /api/conversations/{id}` - Delete
- `GET /api/conversations/{id}/messages` - Get messages

### Diagram Generation
- `POST /api/prompt` - Send message, get AI response
- `GET /health` - Server status

---

## 📝 Usage

1. **Create Chat** → Click "+" in sidebar
2. **Enter Prompt** → e.g., "Create a flowchart for user login"
3. **Wait** → Model generates and validates diagram
4. **View** → Mermaid diagram renders in chat
5. **Export** → Copy code or download as PNG

---

## 🔐 Security

- JWT authentication on all endpoints
- bcrypt password hashing
- Rate limiting (20-60 req/min per endpoint)
- SQLite for dev, PostgreSQL recommended for production
- CORS configured for dev/production modes

---

## 📖 Environment Variables

```env
# LLM
GOOGLE_API_KEY=          # Required: Gemini API key
TAVILY_API_KEY=          # Required: Web search API

# Auth
SECRET_KEY=              # Required: JWT secret (use: openssl rand -hex 32)
ACCESS_TOKEN_EXPIRE_MINUTES=1440

# Database
DATABASE_URL=sqlite+aiosqlite:///./mermaid_app.db
# For PostgreSQL: postgresql+asyncpg://user:pass@localhost/dbname

# Server
DEV_MODE=true            # Set false in production
ALLOWED_ORIGINS=http://localhost:8000,http://127.0.0.1:8000
```

---

## 🚢 Production Deployment

1. Use PostgreSQL instead of SQLite
2. Set `DEV_MODE=false`
3. Configure `ALLOWED_ORIGINS` with your domain
4. Use Gunicorn: `gunicorn -w 4 -k uvicorn.workers.UvicornWorker main:app`
5. Run behind Nginx with HTTPS

---

## 📄 License

MIT License