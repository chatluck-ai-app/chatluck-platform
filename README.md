# Chat Luck AI — Multi-Model Intelligent Agent Platform

Android + Web + one FastAPI backend. Arabic-first, free-tier-first, no keys in clients.

## Status (2026-11-26)

| Phase | State | Evidence |
| --- | --- | --- |
| 1. Architecture & infrastructure | Done (monorepo + CI pending push) | this repo layout |
| 2. Auth & database | Done, tested | 24 pytest passing; register/login/JWT, user isolation |
| 3. AI Provider Gateway | Done, live-verified | real Gemini reply via API; fallback + circuit breaker tested |
| 4. Chat interface (web) | In progress | backend endpoints done (chat, SSE stream, conversations, models, images, memory) |
| 5-12 | Pending | see plan |

## Live verification log

- 2026-11-26: gemini-2.5-flash returns 404 "no longer available to new users" (Google recommends gemini-3.8-flash). Verified working: gemini-3.1-flash-lite, gemini-3-flash-preview. Default model: gemini-3.1-flash-lite.
- Live E2E: register -> POST /v1/chat -> real Arabic answer from Gemini; SSE stream emits token events + done.

## Layout

- `backend/` — FastAPI app (Python 3.10+)
  - `app/providers/` — provider gateway: base contract, Gemini, OpenAI-compatible (Groq, OpenRouter), Pollinations images, router with auto-routing, fallback, circuit breaker
  - `app/api_auth.py`, `app/api_chat.py` — auth, chat (SSE), models, images, memory
  - `tests/` — 24 tests: auth, router, chat, isolation, memory
- `web/` — Next.js + TypeScript (phase 4)
- `android/` — Kotlin + Jetpack Compose (phase 10)

## Rules

- No API keys in clients or committed files. Server-side env only (.env.example is the template).
- No delivery without passing build + tests. Reports state what is done, what is not, and remaining errors.
- Free providers first; honest limits, no fake "unlimited".

## Run backend

```bash
cd backend
pip install -r requirements.txt
cp .env.example .env  # fill GEMINI_API_KEY etc.
uvicorn app.main:app --reload
```

## Test

```bash
cd backend && python3 -m pytest tests/ -q
```
