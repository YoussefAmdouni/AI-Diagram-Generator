# AI Diagram Assistant

A conversational AI assistant that generates and validates Mermaid diagrams, with full user authentication and streaming responses.

## Stack

| Layer    | Tech                                                        |
|----------|-------------------------------------------------------------|
| Backend  | FastAPI, LangChain                                          |
| LLMs     | Gemini 2.5 Flash (main), Gemini 2.0 Flash-Lite (safety)    |
| Tools    | Tavily web search, Mermaid CLI syntax validation            |
| Database | Neon (Postgres) via SQLAlchemy async + asyncpg              |
| Auth     | JWT access tokens (15min) + refresh tokens (30 days, DB-backed) |
| Frontend | Vanilla JS, SSE streaming, Mermaid.js rendering             |

## Architecture

Every message goes through a 4-step pipeline:
```
User message
    │
    ▼
1. Regex sanitizer          — instant, blocks prompt injection patterns
    │
    ▼
2. Flash-Lite safety check  — plain text, single-word verdict (safe/unsafe)
    │
    ▼
3. Flash orchestrator       — routes to "workflow" or "direct"
    │
    ├─► workflow: Mermaid tool loop (generate → validate → fix → repeat)
    └─► direct:   General tool loop (answer + optional web search)
    │
    ▼
4. Full answer sent via SSE
```

## Setup

**Prerequisites:** Python 3.11+, Node (for `mmdc` Mermaid CLI)
```bash
# Install Mermaid CLI
npm install -g @mermaid-js/mermaid-cli

# Install Python deps
cd backend
pip install -r requirements.txt

# Copy and fill in env vars
cp .env.example .env
```

**Required `.env` values:**
```bash
SECRET_KEY=       # min 32 chars — generate with: openssl rand -hex 32
DATABASE_URL=     # postgresql+asyncpg://user:pass@host/neondb
GOOGLE_API_KEY=   # Gemini API key
TAVILY_API_KEY=   # Tavily search API key
RESEND_API_KEY=   # Resend email API key (password reset)
EMAIL_FROM=       # verified sender address in Resend
APP_URL=          # e.g. http://localhost:8000
DEV_MODE=         # true for local dev only — never in production
```

**Run:**
```bash
cd backend/app
uvicorn main:app --reload
```

Open `http://localhost:8000`.

## Auth Flow

- **Register / Login** → returns `access_token` (15 min) + `refresh_token` (30 days)
- **Silent refresh** — frontend automatically exchanges refresh token on 401, no re-login needed
- **Logout** — revokes refresh token server-side
- **Password reset** — email link via Resend, single-use token, expires in 1 hour

## Project Structure
```
├── backend/app/
│   ├── main.py           # FastAPI app, SSE streaming endpoint
│   ├── agent.py          # LLM pipeline (safety → route → tool loop)
│   ├── auth.py           # JWT, refresh tokens, password reset
│   ├── database.py       # SQLAlchemy models
│   ├── tool.py           # Tavily search + Mermaid CLI syntax check
│   ├── email_service.py  # Resend integration
│   ├── logger.py         # JSON structured logging
│   ├── prompt.yaml       # All LLM prompts
│   └── context.py        # Request ID context var
└── frontend/
    ├── index.html
    ├── script.js         # SSE client, silent token refresh, diagram rendering
    ├── style.css
    └── auth.css
```

## Free Tier Services

| Service | Free Tier |
|---|---|
| [Neon](https://neon.tech) | 0.5 GB storage, 1 project |
| [Resend](https://resend.com) | 3,000 emails/month |
| [Tavily](https://tavily.com) | 1,000 searches/month |
| [Google AI Studio](https://aistudio.google.com) | Gemini API free tier |