# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

VakilSuite — SaaS for Allahabad High Court lawyers. Core flow: Case → Document Upload → OCR → AI Extraction → Human Review → Push to Context → Case Summary → Draft Generation (streamed) → Tiptap Editor → Export PDF/DOCX.

Uploaded documents are classified server-side. Searchable PDFs and DOCX files use local text extraction. Scanned PDFs and images are intentionally blocked until a local Indian-language OCR provider is wired in.

## Commands

```bash
# Package management — use uv, not pip
uv add <package>
uv sync

# Dev server
uvicorn app.main:app --reload

# Database
docker-compose up -d              # Start Postgres + Redis
alembic upgrade head              # Run migrations
alembic revision --autogenerate -m "description"  # New migration
python reset_db.py                # Drop + recreate schema
python -m app.seed                # Seed dev data
python check_db.py                # Test DB connection

# Code quality
ruff check .                      # Lint
ruff format .                     # Format
mypy .                            # Type check

# Celery workers — Windows development
# MUST use --pool=solo on Windows (prefork uses fork() which Windows doesn't support)
celery -A app.workers.celery_app worker --loglevel=info -Q extraction --pool=solo
celery -A app.workers.celery_app worker --loglevel=info -Q export --pool=solo
# All queues at once (dev convenience):
celery -A app.workers.celery_app worker --loglevel=info --pool=solo

# Production (Linux) — prefork works fine, no --pool flag needed:
# celery -A app.workers.celery_app worker --loglevel=info -Q extraction
```

## Architecture

### Pattern: Model → Repository → Service → Router

Every feature in `app/features/{feature}/` follows this strict layering:

- **`models.py`** — SQLAlchemy ORM only. No business logic.
- **`repository.py`** — Async DB queries only. No HTTP concepts.
- **`service.py`** — All business logic. Calls repository + external APIs (Claude, Google Doc AI, R2). No HTTP concepts.
- **`router.py`** — Thin HTTP layer only. Calls service, returns response. No business logic here.
- **`schemas.py`** — Pydantic v2 request/response models. Never expose `hashed_password` or raw AI responses.

### Core (`app/core/`)

- **`config.py`** — Pydantic Settings. All env vars loaded here. Import `settings` anywhere.
- **`security.py`** — JWT creation/decode, bcrypt hashing.
- **`dependencies.py`** — FastAPI dependency injection. Key deps: `CurrentUser` (any auth'd user), `LawyerUser` (lawyer role only), `get_case_with_access` (injects Case after checking lawyer_id or case_access).
- **`exceptions.py`** — Custom exception classes that map to standard API error format.

### Database (`app/db/`)

- **`session.py`** — Async SQLAlchemy engine + `get_db` dependency (yields `AsyncSession`).
- **`registry.py`** — **Critical**: All models must be imported here for Alembic to detect them. Add new models here when creating them.
- **`base.py`** — `DeclarativeBase` that all models inherit from.

Migrations live in `app/db/migrations/versions/`. Never edit an applied migration.

### Workers (`app/workers/`)

Document processing, AI extraction, and export jobs run as Celery tasks (Redis broker). HTTP endpoints never block on long-running work — they enqueue and return a `job_id`. Job status is tracked in Redis and polled via `GET /api/v1/jobs/{job_id}`.

### Auth Flow

- Login/Register → `access_token` (15 min JWT) + `refresh_token` (30 days) both set as HttpOnly cookies, access_token also in response body.
- Google OAuth: `id_token` verified via `google-auth` library, auto-creates or links user.
- Auth providers tracked: `AuthProvider.email` vs `AuthProvider.google`.

### API Response Format

All endpoints return:
```json
{ "success": true, "data": { ... } }
{ "success": true, "data": [...], "pagination": { "page", "limit", "total", "total_pages" } }
{ "success": false, "error": { "code": "NOT_FOUND", "message": "...", "details": {} } }
```

### Prompts

AI prompts live in `prompts/` as `.txt` files versioned with code — never hardcode prompts in Python. Prompts exist for: `extraction/{doc_type}`, `summary/case_summary`, `drafts/{draft_type}`, `courtroom/{session_type}`.

## Environment Variables

Required in `.env`:
```
DATABASE_URL=postgresql+asyncpg://vakilsuite:vakilsuite@localhost:5432/vakilsuite
REDIS_URL=redis://localhost:6379/0
SECRET_KEY=<32+ char secret>
GOOGLE_CLIENT_ID=
ANTHROPIC_API_KEY=
R2_ACCOUNT_ID=
R2_ACCESS_KEY_ID=
R2_SECRET_ACCESS_KEY=
R2_BUCKET_NAME=
```
