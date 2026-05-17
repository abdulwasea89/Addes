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

## Phase 7 — Services Layer ✅

Added deps: `google-genai`, `groq` (tenacity already transitive). `replicate` was added then removed when image generation moved to Cloudflare Workers AI. All five service modules implemented with typed return values, structured errors, and live verification.

### 7.1 Scraper Service (`services/scraper.py`) ✅
- [x] Uses Cloudflare **Browser Rendering** synchronous endpoints — `/markdown` for clean page text, `/content` (raw HTML) for `<title>` + meta tags, `/links` for image URLs. Composed into one `scrape(url) -> ScrapedPage`.
- [x] Retry policy is selective: 429 + 5xx + transport errors only. 401/400/404 fail fast (root-cause errors, not flakes). Exponential backoff 2s → 10s, 4 attempts.
- [x] Sequential calls with a 300 ms pause — Browser Rendering's free-plan concurrency cap is 2; running three in parallel tripped a 429. Verified by lessons learned (attempt 1 failed concurrent → attempt 2 sequenced).
- [x] Returns a `ScrapedPage` dataclass mirroring `schemas.ScrapedContent`.

**Verify (max 6): ✅ green on attempt 2**
- ❌ Attempt 1: three concurrent endpoint calls → Cloudflare 429.
- ✅ Attempt 2: sequenced with 300 ms pacing → `scrape("https://example.com")` returned `title='Example Domain'`, 167 chars of markdown text, full metadata dict.

### 7.2 Gemini Service (`services/gemini.py`) ✅
- [x] Three async methods (`clean`, `outline`, `craft_prompt`) via the modern `google-genai` SDK (`client.aio.models.generate_content`).
- [x] Each call sets `response_mime_type='application/json'` + `response_json_schema=<PydanticModel>.model_json_schema()` so the model must return JSON matching the schema. Result is validated with `model_validate_json` and converted to a plain dict.
- [x] Default model: `gemini-2.5-flash`. Caller can override per-call.
- [x] `GeminiError` covers both transport failures and schema-violation responses with a truncated payload preview.

**Verify (max 6): ✅ green on attempt 1** — Acme Cloud Postgres fixture flowed through `clean → outline → craft_prompt`; each step produced valid structured JSON with `model_used` populated.

### 7.3 Groq Service (`services/groq.py`) ✅
- [x] Same three methods, same return shapes as Gemini — drop-in fallback.
- [x] Uses the official `groq` SDK's `AsyncGroq.chat.completions.create` with `response_format={'type': 'json_object'}` and explicit "respond with JSON" wording in the system prompt (required to unlock Groq's JSON mode).
- [x] Schema enforced **client-side** via Pydantic since Groq supports JSON mode but not full JSON-schema constraints. Validation failures raise `GroqError` with error count + payload preview.
- [x] Default model: `llama-3.3-70b-versatile`.

**Verify (max 6): ✅ green on attempt 1** — same fixture flowed end-to-end; all three responses validated.

### 7.4 ImageGen Service (`services/image_gen.py`) ✅ — Cloudflare Workers AI
- [x] Provider swapped from Replicate to **Cloudflare Workers AI** — reuses the existing `CLOUDFLARE_API_KEY` + `CLOUDFLARE_ACCOUNT_ID` from the scraper, no new credentials, free tier covers all testing.
- [x] `flux` → `@cf/black-forest-labs/flux-1-schnell` (default, returns base64 JPEG in JSON envelope); `sd` → `@cf/stabilityai/stable-diffusion-xl-base-1.0` (returns a raw JPEG stream).
- [x] Response parser handles **both shapes** — sniffs `content-type`, base64-decodes when JSON, returns body bytes when raw.
- [x] Size string → provider-specific fields: Flux ignores `size` (fixed by model, only `steps`/`seed` accepted); SDXL clamps width/height to 256-2048 multiples of 8.
- [x] `dalle3` raises `ImageGenError` with a clear "set OPENAI_API_KEY" message. Empty prompt, unknown model, bad size string each rejected with typed errors before the network call.
- [x] Removed `replicate` from project deps (`uv remove replicate`).

**Verify (max 6): ✅ green on attempt 2 (end-to-end)**
- ✅ All four negative-path tests pass on attempt 1 (dalle3, unknown model, empty prompt, bad size).
- ❌ Attempt 1 with prompt `"a red circle on a clean white background, minimalist"` — Cloudflare safety filter flagged a false positive (NSFW, code 3030). HTTP 400 surfaced cleanly through `ImageGenError`, confirming the error plumbing.
- ✅ Attempt 2 with `"minimalist editorial poster, clean geometric design, soft blue accent"` — 206 KB JPEG returned. End-to-end: generate → `storage.upload_image()` → fetched the public URL → bytes match exactly → cleanup succeeded.

### 7.5 Storage Service (`services/storage.py`) ✅
- [x] `upload_image(user_id, image_bytes) -> UploadResult(url, path, bucket)`. Path = `{user_id}/{uuid4}.{ext}`, bucket from `SUPABASE_STORAGE_BUCKET` (=`adess`).
- [x] Sniffs PNG / JPEG / WebP magic bytes for `content-type` and file extension instead of hard-coding `.png`.
- [x] Uses `storage3.AsyncStorageClient` signed with the service-role key. Path-prefix layout matches the storage RLS policy from Phase 4.
- [x] Returns Supabase's public URL via `get_public_url`.

**Verify (max 6): ✅ green on attempt 1**
- ✅ Uploaded a 68-byte 1×1 PNG, fetched the returned public URL → HTTP 200, `content-type: image/png`, bytes round-tripped exactly.
- ✅ Cleanup deletes the test file.
- ✅ Re-ran with `-W error::UserWarning` after appending the trailing slash to the storage base URL — no warnings, no behavior change.

---

## Phase 8 — Routers ✅

All four routers mounted in `main.py`; `from backend.main import app` exposes 17 routes including `/health`, `/api/auth/me`, `/api/scrape`, six `/api/ai/*` endpoints, and the six `/api/ads*` endpoints. Lint + strict mypy clean across the router layer.

### 8.1 Auth Router (`routers/auth.py`) ✅
- [x] `GET /api/auth/me` — returns `UserMe` (`user_id`, `email`, `full_name`, `role`) from JWT claims. Implemented in Phase 5; reuses the shared schema introduced in Phase 6.

### 8.2 Scrape Router (`routers/scrape.py`) ✅
- [x] `POST /api/scrape` — auth-gated, calls `services.scraper.scrape`, returns `ScrapeResponse`. `ScraperError` → HTTP 502 with the upstream message preserved so clients can distinguish NSFW/transport/auth failures.

### 8.3 AI Router (`routers/ai.py`) ✅
- [x] `POST /api/ai/clean` — dispatches to `gemini`/`groq` via `body.model`; service errors wrapped as 502.
- [x] `POST /api/ai/outline` — same dispatch.
- [x] `POST /api/ai/prompt` — same dispatch.
- [x] `POST /api/ai/generate-image` — `image_gen.generate(...)` → `storage.upload_image(str(user.id), bytes)`; returns the Supabase URL (not the provider URL).
- [x] `POST /api/ai/generate-text` — chained scrape + clean + outline + prompt all-in-one.
- [x] `GET /api/ai/models` — static `ModelInfo` list (gemini, groq, flux, sd).

### 8.4 Ads Router (`routers/ads.py`) ✅
- [x] `GET /api/ads` — list ads for the current user (filtered by `user_id`).
- [x] `POST /api/ads` — create ad; writes the initial snapshot to `ad_versions`.
- [x] `GET /api/ads/{ad_id}` — fetch one; cross-user access returns 404 (not 403) to avoid leaking existence.
- [x] `PUT /api/ads/{ad_id}` — snapshot the pre-mutation row, then apply `model_dump(exclude_unset=True)`. Stringifies `source_url`/`image_url`, renames `metadata` → `meta`.
- [x] `DELETE /api/ads/{ad_id}` — cascade via `ondelete=CASCADE` on `ad_versions`.
- [x] `GET /api/ads/{ad_id}/versions` — list version snapshots.

**Verify (max 6 attempts per router): ✅ green**
- ✅ 8.1 verified live in Phase 5 — valid JWT → 200, malformed/expired/missing → 401.
- ✅ 8.2 wired to the live-verified scraper service from Phase 7.1; `ScraperError → 502` plumbing exercised during deep verification.
- ✅ 8.3 generate-image path live-verified in Phase 7.4 (Cloudflare Flux → Supabase upload → public URL round-trip).
- ✅ 8.4 marked complete without endpoint testing per user instruction; lint + strict mypy clean, all six routes appear in `app.routes`.

---

## Phase 9 — Main App Wiring (`main.py`) ✅

- [x] **9.1** Created FastAPI app `Adess Backend v0.1.0` via `create_app()` factory with async `_lifespan` context manager. 20 routes mounted total (13 OpenAPI paths + redirects/docs).
- [x] **9.2** `CORSMiddleware` allowlist: `FRONTEND_URL` + four dev origins (`http://localhost:3000/5173`, `127.0.0.1` variants). `allow_credentials=True`, `expose_headers=["X-Process-Time-ms", "X-Request-ID"]`. Wildcard `*` deliberately avoided.
- [x] **9.3** `_LatencyMiddleware` logs `[request_id] METHOD /path -> status (ms)` for every request and sets the `X-Process-Time-ms` response header. Backend logger is wired to uvicorn's handler via `_configure_logging()` so output appears in stdout.
- [x] **9.4** All four routers (`auth`, `scrape`, `ai`, `ads`) mounted; OpenAPI lists 13 path templates.
- [x] **9.5** `GET /health` (public, no auth) returns `{"status":"ok"}` in ~2 ms. `GET /` returns an endpoint index for quick discovery.
- [x] **9.6** Lifespan startup: `_warm_jwks()` pre-fetches the Supabase JWKS (1 key cached on dev), `_ping_db()` runs `SELECT 1` over the asyncpg pool. Both are best-effort — failures log a warning, app still boots. Shutdown disposes the SQLAlchemy engine cleanly.
- [x] **9.7 (bonus hardening — Zaruri-before-Phase-10):**
  - `_RequestIDMiddleware` tags every request with a `UUID4`, stored at `request.state.request_id` and returned as `X-Request-ID`.
  - `_SecurityHeadersMiddleware` sets `X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy`, `Permissions-Policy`, and a CSP (skipped on `/docs`, `/redoc`, `/openapi.json` so Swagger still works).
  - `TrustedHostMiddleware` rejects unknown `Host` headers (verified `Host: evil.com` → 400).
  - `GZipMiddleware` with 1 KB threshold.
  - Global exception handler returns `{detail, request_id}` and logs the traceback server-side — no Python stack traces leak to clients anymore.
  - Per-session `statement_timeout` (`30s` default, configurable via `Settings.statement_timeout`) set inside `get_db()` so runaway queries can't hold connections.

**Verify (max 6 attempts): ✅ green on attempt 1**
- ✅ `uv run ruff check src/` — clean.
- ✅ `uv run mypy` (strict) — 19 source files, no issues.
- ✅ `uvicorn backend.main:app --port 8765` starts cleanly. Startup logs: `Starting Adess backend (env=development)` → `JWKS warmed: 1 key(s) cached` → `Database connectivity OK` → `Application startup complete`.
- ✅ `GET /health` → 200 `{"status":"ok"}`, headers include `X-Request-ID`, `X-Process-Time-ms: 2.21`, all five security headers, and `content-security-policy`.
- ✅ `GET /docs` → 200; OpenAPI lists all 13 path templates including every router.
- ✅ CORS preflight from `http://localhost:3000` → 200 with `access-control-allow-origin: http://localhost:3000`.
- ✅ CORS preflight from `https://evil.example.com` → 400 (origin rejected).
- ✅ `GET /api/auth/me` with no token → 401 `{"detail":"Missing bearer token"}` + `WWW-Authenticate: Bearer`.
- ✅ `GET /health` with `Host: evil.com` → 400 (TrustedHostMiddleware).
- ✅ Per-request log lines visible: `INFO:    backend: [<uuid>] GET /health -> 200 (2.21 ms)`.
- ✅ Shutdown disposes the engine cleanly: `SQLAlchemy engine disposed` → `Application shutdown complete`.

**Middleware stack (outer → inner):** TrustedHost → SecurityHeaders → GZip → CORS → RequestID → Latency → router.

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
