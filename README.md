# Adess Backend

AI-powered ad creation backend. A user pastes any URL; the service scrapes the page, cleans the content with an LLM, drafts a brand-aligned outline, crafts a minimal image-generation prompt, generates an image, and persists the finished ad — ready for inline editing on the frontend.

This repository contains **the backend only**. All UI (rich editor, image cropping, drag-to-reorder) lives in a separate frontend repo.

---

## Table of Contents

1. [Architecture](#architecture)
2. [Tech Stack](#tech-stack)
3. [Pipeline](#pipeline)
4. [Project Layout](#project-layout)
5. [Prerequisites](#prerequisites)
6. [Setup](#setup)
7. [Environment Variables](#environment-variables)
8. [Running Locally](#running-locally)
9. [API Surface](#api-surface)
10. [Database](#database)
11. [Supabase Storage](#supabase-storage)
12. [Testing](#testing)
13. [Code Quality](#code-quality)
14. [Deployment](#deployment)
15. [Documentation](#documentation)

---

## Architecture

```
            ┌────────────────────────────────────────────┐
            │              FastAPI backend               │
            │  routers/   services/   auth (JWT verify)  │
            └──────────────────┬─────────────────────────┘
                               │
        ┌──────────────────────┼──────────────────────────┐
        ▼                      ▼                          ▼
   ┌─────────┐            ┌─────────┐               ┌──────────┐
   │Supabase │            │Supabase │               │ External │
   │Postgres │            │ Auth    │               │  AI APIs │
   │ +Storage│            │(Google/ │               │ Gemini   │
   │         │            │ Email)  │               │ Groq     │
   └─────────┘            └─────────┘               │ DALL-E   │
                                                    │ Flux/SD  │
                                                    └──────────┘
```

The backend is stateless. Authentication is delegated entirely to Supabase Auth — the backend only verifies JWTs and reads the `sub` claim.

---

## Tech Stack

| Layer        | Choice                                       |
|--------------|----------------------------------------------|
| Language     | Python 3.12                                  |
| Framework    | FastAPI                                      |
| Server       | Uvicorn (async, uvloop)                      |
| ORM          | SQLAlchemy 2.0 async                         |
| DB driver    | asyncpg                                      |
| Database     | PostgreSQL 15 (Supabase)                     |
| Auth         | Supabase Auth (Google OAuth + Magic Link)    |
| Storage      | Supabase Storage (generated images)          |
| Validation   | Pydantic v2 + pydantic-settings              |
| HTTP client  | httpx                                        |
| Lint/format  | Ruff                                         |
| Type checker | mypy (strict)                                |
| Tests        | pytest + pytest-asyncio                      |
| Packaging    | uv (`pyproject.toml`)                        |

---

## Pipeline

```
URL ─▶ /api/scrape ─▶ /api/ai/clean ─▶ /api/ai/outline
                                              │
                                              ▼
                              /api/ai/prompt ─▶ /api/ai/generate-image
                                                       │
                                          (image bytes uploaded
                                           to Supabase Storage)
                                                       │
                                                       ▼
                                                  POST /api/ads
                                                       │
                                                       ▼
                                                 Frontend editor
```

Why images go through Supabase Storage: provider URLs (DALL-E, Replicate) expire within minutes to hours. Uploading the bytes to a Supabase bucket gives a stable URL that remains viewable for the lifetime of the ad.

---

## Project Layout

```
backend/
├── src/backend/
│   ├── __init__.py
│   ├── cli.py            # console entry point (uvicorn launcher)
│   ├── main.py           # FastAPI app factory (Phase 9)
│   ├── config.py         # pydantic-settings
│   ├── database.py       # SQLAlchemy async engine + session
│   ├── models.py         # ORM models
│   ├── schemas.py        # Pydantic request/response models
│   ├── auth.py           # Supabase JWT verification
│   ├── routers/          # FastAPI routers (ads, auth, scrape, ai)
│   └── services/         # scraper, gemini, groq, image_gen, storage
├── tests/
├── BACKEND_SPEC.md       # full specification
├── TASK.md               # build plan with verification steps
├── .env.example          # template — copy to .env
├── pyproject.toml
└── uv.lock
```

---

## Prerequisites

- Python 3.12 or later
- [uv](https://github.com/astral-sh/uv) 0.9 or later
- A Supabase project (free tier is fine)
- API keys: Gemini, Groq, OpenAI (DALL-E), Replicate, Cloudflare

---

## Setup

```bash
# 1. Clone and enter
git clone <repo-url>
cd backend

# 2. Install dependencies (creates .venv, installs dev deps)
uv sync

# 3. Configure environment
cp .env.example .env
# edit .env with your real keys

# 4. Apply database schema in the Supabase SQL editor
# (paste the SQL from BACKEND_SPEC.md sections 5.1 – 5.3)

# 5. Create the Supabase Storage bucket
# Dashboard -> Storage -> New bucket -> name: ad-images, public-read

# 6. Smoke-test installation
uv run python -c "import fastapi, sqlalchemy, asyncpg, supabase; print('ok')"
```

---

## Environment Variables

All variables live in `.env`. See [`.env.example`](.env.example) for the authoritative list and inline guidance. Highlights:

| Group       | Variable                          | Purpose                                       |
|-------------|-----------------------------------|-----------------------------------------------|
| App         | `APP_ENV`                         | `development` / `staging` / `production`      |
| App         | `FRONTEND_URL`                    | CORS origin                                   |
| Supabase    | `SUPABASE_URL`                    | Project URL                                   |
| Supabase    | `SUPABASE_ANON_KEY`               | Legacy anon key (or `SUPABASE_PUBLISHABLE_KEY`) |
| Supabase    | `SUPABASE_SERVICE_ROLE_KEY`       | Legacy admin key (or `SUPABASE_SECRET_KEY`)   |
| Supabase    | `SUPABASE_JWT_SECRET`             | Used by auth middleware to verify JWTs        |
| Supabase    | `SUPABASE_STORAGE_BUCKET`         | Bucket name (`ad-images`)                     |
| Database    | `DATABASE_URL`                    | `postgresql+asyncpg://…` (Session Pooler)     |
| AI          | `GEMINI_API_KEY` etc.             | Provider credentials                          |
| Scraping    | `CLOUDFLARE_API_KEY`, `..._ACCOUNT_ID` | Cloudflare scraping API                  |

Supabase is in the middle of migrating from JWT-based legacy keys to the `sb_publishable_…` / `sb_secret_…` scheme. The `.env.example` documents both — fill in whichever your project uses.

For `DATABASE_URL`, the **Session Pooler** (port 5432) is the recommended choice for this long-lived FastAPI service: IPv4-friendly, supports prepared statements, no asyncpg quirks.

---

## Running Locally

```bash
# Option A — via the console script
uv run backend

# Option B — via uvicorn directly
uv run uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

Health check: <http://localhost:8000/health>
Interactive API docs: <http://localhost:8000/docs>

---

## API Surface

| Method | Path                          | Description                                  |
|--------|-------------------------------|----------------------------------------------|
| GET    | `/health`                     | Liveness probe                               |
| GET    | `/api/auth/me`                | Current user (from JWT)                      |
| POST   | `/api/scrape`                 | Scrape a URL via Cloudflare                  |
| POST   | `/api/ai/clean`               | LLM cleans scraped content                   |
| POST   | `/api/ai/outline`             | LLM drafts headline / body / CTA / keywords  |
| POST   | `/api/ai/prompt`              | LLM crafts minimal image prompt              |
| POST   | `/api/ai/generate-image`      | Generates image; uploads to Supabase Storage |
| POST   | `/api/ai/generate-text`       | Legacy all-in-one copy generator             |
| GET    | `/api/ai/models`              | List supported AI models                     |
| GET    | `/api/ads`                    | List ads for current user                    |
| POST   | `/api/ads`                    | Create ad                                    |
| GET    | `/api/ads/{ad_id}`            | Fetch ad (RLS enforces ownership)            |
| PUT    | `/api/ads/{ad_id}`            | Update ad (snapshots previous version)       |
| DELETE | `/api/ads/{ad_id}`            | Delete ad                                    |
| GET    | `/api/ads/{ad_id}/versions`   | Version history                              |

Every protected route requires `Authorization: Bearer <supabase_jwt>`.

Full request/response shapes: see [`BACKEND_SPEC.md`](BACKEND_SPEC.md) section 6.

---

## Database

Three tables, all with Row-Level Security enabled:

- `user_profiles` — extends `auth.users`
- `ads` — the canonical ad row; pipeline metadata stored as `JSONB`
- `ad_versions` — append-only audit trail of edits

The SQL for tables, indexes, and RLS policies is in [`BACKEND_SPEC.md`](BACKEND_SPEC.md) section 5. Paste it into the Supabase SQL editor; SQLAlchemy models in `src/backend/models.py` mirror these tables.

---

## Supabase Storage

Generated images are uploaded to the `ad-images` bucket at `{user_id}/{uuid}.png`. The `/api/ai/generate-image` endpoint never returns the provider's temporary URL — only the persisted Supabase URL. This guarantees the image is viewable while testing, even hours after generation.

Bucket policy: public read, authenticated write. Configure under Dashboard → Storage → Policies.

---

## Testing

```bash
# Run the full test suite
uv run pytest

# Run a specific file
uv run pytest tests/test_ads.py -v
```

Tests mock external APIs (Gemini, OpenAI, Cloudflare) so they run offline. End-to-end pipeline verification against a live Supabase project is documented as Phase 10 in [`TASK.md`](TASK.md).

---

## Code Quality

```bash
# Lint
uv run ruff check src/ tests/

# Format
uv run ruff format src/ tests/

# Type-check (strict mode)
uv run mypy
```

Configuration lives in `pyproject.toml` under `[tool.ruff]` and `[tool.mypy]`.

---

## Deployment

The backend is shipped as a Docker image (planned, Phase 12 in `TASK.md`):

```bash
docker build -t adforge-backend .
docker run -p 8000:8000 --env-file .env adforge-backend
```

Production checklist lives in [`BACKEND_SPEC.md`](BACKEND_SPEC.md) section 13: database schema applied, RLS policies, storage bucket created, reverse proxy with SSL, uptime monitoring on `/health`.

---

## Documentation

- [`BACKEND_SPEC.md`](BACKEND_SPEC.md) — full specification (architecture, schemas, endpoints, dependencies, risks).
- [`TASK.md`](TASK.md) — sequential build plan with a verification step per phase (max 6 attempts before marking BLOCKED).
- [`.env.example`](.env.example) — annotated environment variable template.

---

## License

MIT (see `pyproject.toml`).
