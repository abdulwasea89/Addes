# AdForge Backend — Step-by-Step Task List

A sequential build plan for the AdForge backend per `BACKEND_SPEC.md`. Each phase has discrete, verifiable tasks. Check off as you go.

---

## Verification Rule (applies to every phase)

After finishing each phase, run its **Verify** step before moving on. If the verification fails, retry the fix **up to 6 attempts maximum**. Do not exceed 6.

- **Attempts 1–2:** re-run with minor fix (typo, missing import, env var).
- **Attempts 3–4:** read related code/logs, fix root cause.
- **Attempts 5–6:** narrow the failure (write a minimal repro, isolate the broken layer).
- **After 6:** stop, mark the phase **BLOCKED** in this file with the last error, and surface it to the user before continuing. Never silently skip.

Each phase below has a `**Verify:**` block listing the exact check to run.

---

## Phase 0 — Project Setup ✅

- [x] **0.1** Confirm Python 3.11+ is active (Python 3.12.3, uv 0.9.18).
- [x] **0.2** Dependency manager: `uv`; `pyproject.toml` upgraded with ruff/mypy/pytest config and dev-deps group.
- [x] **0.3** Added base dependencies (fastapi, uvicorn[standard], sqlalchemy[asyncio], asyncpg, pydantic, pydantic-settings, python-dotenv, httpx, python-jose[cryptography], supabase, python-multipart).
- [x] **0.4** Created `.env.example` with all spec §9 vars plus `DATABASE_URL` and `SUPABASE_JWT_SECRET`.
- [x] **0.5** Created local `.env` (copied from template; user fills in real keys).
- [x] **0.6** `.gitignore` covers `.env*`, caches, virtualenvs, IDE/OS files; `.env.example` is preserved.
- [x] **Bonus:** Console entry point moved from `__init__.py` to `backend/cli.py`.

**Verify (max 6 attempts): ✅ passed attempt 1**
- ✅ `uv sync` exits 0.
- ✅ `python -c "import fastapi, sqlalchemy, asyncpg, httpx, supabase, pydantic_settings, jose"` — ok.
- ✅ `.env` is git-ignored; `.env.example` is tracked.
- ✅ `uv run ruff check src/` — all checks pass.
- ✅ `from backend.cli import main` — importable.

---

## Phase 1 — Directory Scaffolding

- [ ] **1.1** Create the structure under `src/backend/`:
  ```
  src/backend/
  ├── __init__.py
  ├── main.py
  ├── config.py
  ├── database.py
  ├── models.py
  ├── schemas.py
  ├── auth.py
  ├── routers/
  │   ├── __init__.py
  │   ├── ads.py
  │   ├── auth.py
  │   ├── scrape.py
  │   └── ai.py
  └── services/
      ├── __init__.py
      ├── scraper.py
      ├── gemini.py
      ├── groq.py
      ├── image_gen.py
      └── storage.py
  ```
- [ ] **1.2** Add `tests/` directory with `test_ads.py` and `test_ai.py` placeholders.

**Verify (max 6 attempts):**
- `python -c "import backend, backend.routers, backend.services"` succeeds.
- Every file in the tree above exists (use `find src/backend -name '*.py'`).

---

## Phase 2 — Configuration

- [ ] **2.1** Implement `config.py` using `pydantic-settings`:
  - Load all env vars from spec §9.
  - Expose a cached `get_settings()` dependency.
- [ ] **2.2** Validate required keys at startup; fail fast if missing.

**Verify (max 6 attempts):**
- `python -c "from backend.config import get_settings; s=get_settings(); print(s.SUPABASE_URL[:20])"` prints the URL prefix.
- Temporarily unset one required var → process exits with a clear validation error.

---

## Phase 3 — Database Layer

- [ ] **3.1** Implement `database.py`:
  - Async SQLAlchemy engine using `asyncpg` and Supabase Postgres URL.
  - `AsyncSession` factory.
  - `get_db()` dependency.
- [ ] **3.2** Implement `models.py` (SQLAlchemy 2.0 declarative):
  - `UserProfile` → `user_profiles` table.
  - `Ad` → `ads` table (with `metadata` as `JSONB`).
  - `AdVersion` → `ad_versions` table.
- [ ] **3.3** Run the SQL from spec §5 in the Supabase SQL editor:
  - Create all three tables.
  - Enable RLS.
  - Apply all RLS policies.
- [ ] **3.4** Confirm tables exist in Supabase dashboard.

**Verify (max 6 attempts):**
- Async script connects with `asyncpg` and runs `SELECT 1` — returns `1`.
- `SELECT count(*) FROM ads;` returns `0` (table exists, RLS allows service role).
- `SELECT relrowsecurity FROM pg_class WHERE relname='ads';` returns `true`.

---

## Phase 4 — Supabase Storage Bucket

- [ ] **4.1** In Supabase dashboard → Storage, create bucket `ad-images`.
- [ ] **4.2** Make bucket public-read (so test browser can view generated images).
- [ ] **4.3** Restrict writes to authenticated users via storage policy.
- [ ] **4.4** Confirm `SUPABASE_STORAGE_BUCKET=ad-images` is in `.env`.

**Verify (max 6 attempts):**
- Upload a 1×1 PNG via `supabase-py` storage client → returns a path.
- Fetch the returned public URL with `httpx.get` → HTTP 200, `content-type: image/png`.
- Open the URL in a browser → image renders.

---

## Phase 5 — Auth Middleware

- [ ] **5.1** Implement `auth.py`:
  - Fetch Supabase JWKS from `{SUPABASE_URL}/auth/v1/.well-known/jwks.json`.
  - Cache JWKS in-memory with 1-hour TTL.
  - `verify_jwt(token)` — verifies signature, expiry, issuer; returns `user_id` (`sub`).
- [ ] **5.2** Implement `get_current_user()` FastAPI dependency:
  - Reads `Authorization: Bearer <jwt>` header.
  - Returns `user_id` or raises `401`.
- [ ] **5.3** Test with a real Supabase JWT (login from frontend or Supabase dashboard).

**Verify (max 6 attempts):**
- Call any protected route with no header → 401.
- Call with `Authorization: Bearer <bad>` → 401.
- Call with a valid Supabase JWT → 200 and the `user_id` matches the JWT `sub`.

---

## Phase 6 — Pydantic Schemas

- [ ] **6.1** In `schemas.py`, define request/response models for:
  - `UserMe` (GET /api/auth/me response)
  - `AdCreate`, `AdUpdate`, `AdResponse`, `AdListResponse`
  - `AdVersionResponse`
  - `ScrapeRequest`, `ScrapeResponse`
  - `CleanRequest`, `CleanResponse`
  - `OutlineRequest`, `OutlineResponse`
  - `PromptRequest`, `PromptResponse`
  - `ImageRequest`, `ImageResponse` (includes `image_url`, `storage_path`)
  - `TextGenRequest`, `TextGenResponse`

**Verify (max 6 attempts):**
- `python -c "from backend.schemas import AdCreate, AdResponse, ImageResponse; AdCreate(title='t')"` succeeds.
- Invalid payload (e.g. missing `title`) raises `ValidationError`.

---

## Phase 7 — Services Layer

### 7.1 Scraper Service (`services/scraper.py`)
- [ ] Implement `scrape(url)` calling Cloudflare's scraping API.
- [ ] Return `{title, description, text, images, metadata}`.
- [ ] Add retry with exponential backoff; raise `502` on persistent failure.

### 7.2 Gemini Service (`services/gemini.py`)
- [ ] Implement `clean(raw_content)` — structured JSON output (brand_name, tagline, etc.).
- [ ] Implement `outline(cleaned_data)` — returns headline/body/CTA/keywords.
- [ ] Implement `craft_prompt(outline, cleaned_data)` — minimal brand-aligned image prompt.

### 7.3 Groq Service (`services/groq.py`)
- [ ] Mirror Gemini's three methods as fallback (Llama/Mixtral).

### 7.4 ImageGen Service (`services/image_gen.py`)
- [ ] Implement `generate(prompt, model, size)`:
  - `dalle3` → OpenAI Images API.
  - `flux` / `sd` → Replicate API.
- [ ] **Return raw image bytes**, not the provider URL.

### 7.5 Storage Service (`services/storage.py`)
- [ ] Implement `upload_image(user_id, image_bytes) -> {url, path}`:
  - Use `supabase-py` storage client with service-role key.
  - Path: `{user_id}/{uuid4}.png`.
  - Bucket: `ad-images`.
  - Return public URL + storage path.

**Verify each service (max 6 attempts per service):**
- **Scraper:** call `scrape("https://example.com")` → returns dict with non-empty `title` and `text`.
- **Gemini:** call `clean({...})` with a small fixture → returns dict with `brand_name` and `tone`.
- **Groq:** same fixture as Gemini → returns same shape.
- **ImageGen:** `generate("a red circle", "dalle3", "1024x1024")` → returns bytes starting with PNG/JPEG magic header.
- **Storage:** `upload_image("test-user", b"<png_bytes>")` → returned URL is reachable (HTTP 200) and points to `ad-images/test-user/...`.

---

## Phase 8 — Routers

### 8.1 Auth Router (`routers/auth.py`)
- [ ] `GET /api/auth/me` — returns `{user_id, email, full_name}` from JWT claims.

### 8.2 Scrape Router (`routers/scrape.py`)
- [ ] `POST /api/scrape` — calls scraper service, returns extracted content.

### 8.3 AI Router (`routers/ai.py`)
- [ ] `POST /api/ai/clean` — wraps Gemini/Groq `clean`.
- [ ] `POST /api/ai/outline` — wraps `outline`.
- [ ] `POST /api/ai/prompt` — wraps `craft_prompt`.
- [ ] `POST /api/ai/generate-image`:
  - Call `image_gen.generate(...)` to get bytes.
  - Call `storage.upload_image(user_id, bytes)` to persist.
  - Return Supabase URL (not the provider URL).
- [ ] `POST /api/ai/generate-text` — legacy all-in-one copy generator.
- [ ] `GET /api/ai/models` — static list of supported models.

### 8.4 Ads Router (`routers/ads.py`)
- [ ] `GET /api/ads` — list ads for current user.
- [ ] `POST /api/ads` — create ad (also write first row to `ad_versions`).
- [ ] `GET /api/ads/{ad_id}` — fetch one (RLS will enforce ownership).
- [ ] `PUT /api/ads/{ad_id}` — update; snapshot old state to `ad_versions`.
- [ ] `DELETE /api/ads/{ad_id}` — delete.
- [ ] `GET /api/ads/{ad_id}/versions` — list versions.

**Verify each router (max 6 attempts per router):**
- **Auth router:** `GET /api/auth/me` with valid JWT → 200, returns `user_id` matching JWT.
- **Scrape router:** `POST /api/scrape` with `https://example.com` → 200, non-empty content.
- **AI router (clean/outline/prompt):** chained calls return well-formed JSON at each step.
- **AI router (generate-image):** response `image_url` host equals `<project>.supabase.co`; opening the URL returns the image.
- **Ads router:** create → list → get → update → versions → delete; each returns 200 and respects RLS (no cross-user access).

---

## Phase 9 — Main App Wiring (`main.py`)

- [ ] **9.1** Create FastAPI app with title/version.
- [ ] **9.2** Add CORS middleware allowing `FRONTEND_URL`.
- [ ] **9.3** Add a request latency logging middleware (for success metrics).
- [ ] **9.4** Register all routers under `/api`.
- [ ] **9.5** Add `GET /health` returning `{status: "ok"}`.
- [ ] **9.6** Add startup hook: warm JWKS cache, confirm DB connectivity.

**Verify (max 6 attempts):**
- `uvicorn backend.main:app --reload` starts without exceptions.
- `curl http://localhost:8000/health` → `{"status":"ok"}`.
- `curl http://localhost:8000/docs` → OpenAPI page loads and lists every router.
- CORS preflight from `FRONTEND_URL` returns the expected `Access-Control-Allow-Origin` header.

---

## Phase 10 — End-to-End Pipeline Test

Manually verify the full flow with a real Supabase JWT:

- [ ] **10.1** `POST /api/scrape` with a real URL → returns content.
- [ ] **10.2** `POST /api/ai/clean` with scraped output → returns brand data.
- [ ] **10.3** `POST /api/ai/outline` → returns headline/body/CTA.
- [ ] **10.4** `POST /api/ai/prompt` → returns image prompt.
- [ ] **10.5** `POST /api/ai/generate-image` → returns a **Supabase Storage URL**; open it in the browser to confirm the image renders.
- [ ] **10.6** `POST /api/ads` with all data → row saved.
- [ ] **10.7** `GET /api/ads` → confirm ad appears.
- [ ] **10.8** `PUT /api/ads/{id}` → confirm update + version snapshot.

**Verify (max 6 attempts):**
- All 8 steps above succeed in one continuous run with the same JWT.
- The final ad row in `ads` has `image_url` pointing to Supabase Storage.
- Opening that `image_url` in a browser renders the image.

---

## Phase 11 — Tests

- [ ] **11.1** `tests/test_ads.py` — CRUD happy path + 401 unauth.
- [ ] **11.2** `tests/test_ai.py` — mock external APIs, assert pipeline shape.
- [ ] **11.3** Add `pytest` + `pytest-asyncio` + `httpx` test client to dev deps.

**Verify (max 6 attempts):**
- `pytest -q` exits 0 with all tests passing.
- Coverage of routers ≥ happy-path + 401 case.

---

## Phase 12 — Dockerization

- [ ] **12.1** Write `Dockerfile` (python:3.11-slim, install deps, copy src, run `uvicorn`).
- [ ] **12.2** Add `docker-compose.yml` for local dev with `.env` file.
- [ ] **12.3** Build: `docker build -t adforge-backend .`.
- [ ] **12.4** Run: `docker run -p 8000:8000 --env-file .env adforge-backend`.
- [ ] **12.5** Hit `http://localhost:8000/health` → expect 200.

**Verify (max 6 attempts):**
- `docker build` exits 0.
- Container starts and stays up for 30s without crash-loop.
- `curl localhost:8000/health` from host → 200.
- Re-run the Phase 10 end-to-end test against the containerized service — passes.

---

## Phase 13 — Deployment Prep

- [ ] **13.1** Complete the deployment checklist in spec §13.
- [ ] **13.2** Confirm reverse proxy + SSL plan (Nginx / Caddy / Traefik).
- [ ] **13.3** Set up uptime monitoring on `/health`.

---

**Order matters:** Do not skip ahead. Phases 2 → 5 are prerequisites for every router. Phase 4 (Storage bucket) must exist before testing `/api/ai/generate-image`.
