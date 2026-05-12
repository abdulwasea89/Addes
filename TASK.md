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

## Phase 1 — Directory Scaffolding ✅

- [x] **1.1** Created the structure under `src/backend/`:
  - `__init__.py`, `cli.py`, `main.py` (FastAPI factory placeholder), `config.py`, `database.py`, `models.py`, `schemas.py`, `auth.py`
  - `routers/` — `__init__.py`, `ads.py`, `auth.py`, `scrape.py`, `ai.py` (each exports an `APIRouter` with prefix + tag)
  - `services/` — `__init__.py` (with module-level docstring listing each wrapper), `scraper.py`, `gemini.py`, `groq.py`, `image_gen.py`, `storage.py`
  - Every module starts with `from __future__ import annotations` and a docstring saying which phase implements it.
- [x] **1.2** Added `tests/` with `__init__.py`, `conftest.py` (placeholder for shared fixtures), `test_ads.py`, `test_ai.py`.

**Verify (max 6 attempts): ✅ passed attempt 1**
- ✅ All 23 files present (`find src/backend tests -name '*.py'`).
- ✅ Every submodule importable; `from backend.main import app` yields a `FastAPI("Adess Backend")` instance.
- ✅ `uv run ruff check src/ tests/` — clean.
- ✅ `uv run mypy` (strict) — 19 source files, no issues.
- ✅ `pytest --collect-only` — runs cleanly (exit 5 = no tests collected yet, expected).

---

## Phase 2 — Configuration ✅

- [x] **2.1** Implemented `config.py` with pydantic-settings v2:
  - `Settings(BaseSettings)` loads from env / `.env`, case-insensitive, extra fields ignored, whitespace stripped.
  - All secret fields use `SecretStr` (never leak in repr/logs).
  - Typed `AppEnv` enum, `AnyHttpUrl` URLs, `LogLevel` literal.
  - Properties: `is_production`, `public_supabase_key` (publishable > anon), `admin_supabase_key` (secret > service_role).
  - `get_settings()` cached with `lru_cache(maxsize=1)`.
- [x] **2.2** Two `model_validator(mode="after")` checks for fail-fast:
  - Missing public key → clear error citing both legacy and new env var names.
  - Missing admin key → same.
  - `DATABASE_URL` not using `postgresql+asyncpg://` → rejected.

**Verify (max 6 attempts): ✅ passed attempt 1**
- ✅ Ruff & strict mypy clean (19 source files).
- ✅ `get_settings()` returns a cached singleton.
- ✅ `SecretStr` fields print as `**********`.
- ✅ Missing `SUPABASE_JWT_SECRET` → `ValidationError`.
- ✅ Wrong DB driver → caught with helpful message.
- ✅ Missing public-key pair → caught with helpful message.

---

## Phase 3 — Database Layer ✅

- [x] **3.1** Implemented `database.py`:
  - Lazy `get_engine()` / `get_sessionmaker()` so importing is cheap.
  - `get_db()` async generator with commit/rollback semantics.
  - `dispose_engine()` for FastAPI shutdown lifespan.
  - Pool: `pool_pre_ping=True`, size 5 + overflow 10.
  - Auto-detects transaction-pooler URL and sets `statement_cache_size=0`.
- [x] **3.2** Implemented `models.py` (SQLAlchemy 2.0 `Mapped` / `mapped_column`):
  - `UserProfile`, `Ad`, `AdVersion` with FK cascades.
  - `metadata` JSONB column kept under the Python attribute name `meta` (avoids clash with SQLAlchemy's `metadata` attr).
  - Server-side defaults (`gen_random_uuid()`, `NOW()`) so dashboard/REST inserts work too.
  - `CheckConstraint` on `Ad.status`; `relationship` between `Ad` ↔ `AdVersion` with `cascade="all, delete-orphan"`.
- [x] **3.3** Wrote `sql/schema.sql` — single idempotent file with tables, indexes, RLS, policies, and `updated_at` trigger. Paste into Supabase SQL editor.
- [x] **3.4** `sql/schema.sql` pasted into Supabase SQL editor — all three `public.*` tables exist with RLS enabled and 7 policies registered.

**Verify (max 6 attempts): ✅ green**
- ✅ Ruff & strict mypy clean.
- ✅ `Base.metadata` contains exactly `user_profiles`, `ads`, `ad_versions`.
- ✅ Live DB connection works via Supabase **Session Pooler** (`aws-1-ap-northeast-1`, port 5432). PostgreSQL 17.6.
- ✅ `public.{user_profiles, ads, ad_versions}` all exist; `relrowsecurity = true` on each.
- ✅ Policies present: 4 on `ads`, 2 on `user_profiles`, 1 on `ad_versions` (7 total).
- ✅ `SELECT count(*) FROM public.ads` → 0 (table queryable as service role).

**Lessons learned during verification (attempt-by-attempt):**
1. First attempt: ruff flagged unsorted imports → auto-fixed with `ruff check --fix`.
2. Second attempt: `.env` had `postgresql://` (sync driver) → validator rejected it correctly; updated to `postgresql+asyncpg://`. Password contained literal `@` characters → URL-encoded as `%40`.
3. Third attempt: direct connection (`db.<ref>.supabase.co`) is **IPv6-only** by default; switched to Session Pooler (`aws-1-ap-northeast-1.pooler.supabase.com:5432`) with tenant-prefixed username `postgres.<project-ref>`.

---

## Phase 4 — Supabase Storage Bucket ✅

- [x] **4.1** Bucket `adess` exists in the Supabase Storage dashboard.
- [x] **4.2** Bucket marked public-read via API (`public=True`); also locked down with a 10 MB file size limit and MIME whitelist (`image/png`, `image/jpeg`, `image/webp`).
- [x] **4.3** Restricted writes to authenticated users via storage RLS — uploads/updates/deletes only inside the user's own `{user_id}/...` folder. Policies live in `sql/storage_policies.sql`.
- [x] **4.4** `SUPABASE_STORAGE_BUCKET=adess` already in `.env`.

**Verify (max 6 attempts): ✅ green on attempt 1**
- ✅ Uploaded a 1×1 PNG via `supabase-py` storage client → 67 bytes stored.
- ✅ Public URL: `https://<project>.supabase.co/storage/v1/object/public/adess/<path>` — fetched HTTP 200, `content-type: image/png`, bytes match.
- ✅ Delete works.
- ✅ Storage policies present: `adess: public read` (SELECT), `adess: owner insert` (INSERT), `adess: owner update` (UPDATE), `adess: owner delete` (DELETE).

---

## Phase 5 — Auth Middleware ✅

- [x] **5.1** Implemented `backend/auth.py` supporting **both** Supabase signing schemes:
  - **Asymmetric (ES256 / RS256)** — modern projects. Reads `kid` from the token header, fetches `{SUPABASE_URL}/auth/v1/.well-known/jwks.json`, caches keys in-process for 1 hour, thread-safe. Refreshes once on `kid` miss for rotation handling. Resilient to transient JWKS fetch errors (keeps stale cache).
  - **Symmetric (HS256)** — legacy fallback using `SUPABASE_JWT_SECRET`.
  - Algorithm is picked from the token header — no project-type config needed.
  - Enforces `exp`, `sub`, and `aud=authenticated`. Wide `JWTError` hierarchy collapsed into a single uniform 401.
  - Returns a typed, frozen `CurrentUser` dataclass with `id`, `email`, `role`, full `claims` dict.
- [x] **5.2** `get_current_user` dependency uses `HTTPBearer(auto_error=False)` so we can return our own uniform 401 with `WWW-Authenticate: Bearer`. Validates UUID-shaped subject before returning.
- [x] **5.3** Wired `GET /api/auth/me` (in `routers/auth.py`) returning `{user_id, email, role}`. Mounted in `main.py` along with `/health`.

**Verify (max 6 attempts): ✅ green on attempt 1**
- ✅ Ruff & strict mypy clean (19 source files; `types-python-jose` added as dev dep).
- ✅ `/health` (public) → 200.
- ✅ `/api/auth/me` no header → 401, `Missing bearer token`.
- ✅ Malformed token → 401.
- ✅ Wrong signature → 401, `Signature verification failed`.
- ✅ Expired token → 401, `Signature has expired`.
- ✅ Valid forged token → 200; `user_id`, `email`, `role` round-trip correctly.

---

## Phase 6 — Pydantic Schemas ✅

- [x] **6.1** Implemented every request/response model in `schemas.py`:
  - `UserMe` (`GET /api/auth/me` response — `user_id`, `email`, `full_name`, `role`).
  - `AdCreate`, `AdUpdate`, `AdResponse`, `AdListResponse`.
  - `AdVersionResponse`, `AdVersionListResponse`.
  - `ScrapeRequest`, `ScrapedContent`, `ScrapeResponse`.
  - `CleanRequest`, `CleanResponse`.
  - `OutlineRequest`, `OutlineResponse`.
  - `PromptRequest`, `PromptResponse`.
  - `ImageRequest`, `ImageResponse` (carries `image_url` + `storage_path`).
  - `TextGenRequest`, `TextGenResponse`.
  - `ModelInfo`, `ModelsResponse` (for `GET /api/ai/models`).
- [x] **6.2** Hardened with a `_Strict` base: `extra="forbid"`, whitespace stripping, enum-by-value.
- [x] **6.3** `AdResponse` / `AdVersionResponse` read directly from ORM rows via `from_attributes=True`; ORM attribute `meta` is mapped to public field `metadata` via `AliasChoices` so the JSON contract matches the spec without renaming the SQLAlchemy attribute.
- [x] **6.4** `AdStatus`, `LLMModel`, `ImageModel`, `ImageSize` are `Literal` types — bad enums get rejected at validation time, not by the provider.
- [x] **6.5** Re-wired `routers/auth.py` to return the shared `UserMe` schema (drops the duplicate inline model; populates `full_name` from JWT `user_metadata`).

**Verify (max 6 attempts): ✅ green on attempt 1**
- ✅ `uv run ruff check src/` — clean.
- ✅ `uv run mypy` (strict) — 19 source files, no issues.
- ✅ Smoke script exercises 12 scenarios — every one passes:
  - Happy path for `AdCreate`, `AdUpdate`, `ImageResponse`, `AdResponse` (incl. `meta`→`metadata` alias round-trip), `AdListResponse`, `ModelsResponse`, `UserMe`, every AI pipeline request.
  - Rejects: missing `title`, bad `status`, extra fields, malformed URL, empty `title`, invalid image size.

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
