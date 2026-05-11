# AdForge — Backend Specification v3.0

## Project Idea

**AdForge** is an AI-powered ad creation platform. A user pastes any URL (e.g., `abc.com`). The backend scrapes the page, cleans and structures the raw content using an LLM, writes a brand-aligned content outline, then crafts a minimal, brand-focused image generation prompt. The result is a complete ad (title, description, keywords, generated image) that the user can then edit in a rich frontend editor. Every text field and image is editable. The backend is a stateless FastAPI service that handles scraping, AI text generation, image generation, and ad CRUD. Authentication is delegated entirely to Supabase Auth (Google OAuth + Email Magic Link). The rich editor lives on the frontend using free, open-source tools.

---

## 1. Overview

| Item | Detail |
|------|--------|
| **Project** | AdForge Backend |
| **Framework** | FastAPI 0.109+ (Python 3.11+) |
| **Server** | Uvicorn 0.27+ |
| **ORM** | SQLAlchemy 2.0+ (async) |
| **Database** | PostgreSQL 15+ (Supabase) |
| **Driver** | asyncpg 0.29+ |
| **Auth** | Supabase Auth (Google OAuth + Email Magic Link) |
| **Deployment** | Docker (cloud provider TBD) |

**Core Responsibility:** Expose a stateless API that accepts a Supabase JWT on every request, verifies it, and performs the full ad generation pipeline: **Scrape → Clean → Outline → Prompt → Image → Save**.

---

## 2. The Ad Generation Pipeline

```
User Input (URL)
       │
       ▼
┌──────────────┐
│   /scrape    │  ──▶ Cloudflare API extracts raw page content
└──────┬───────┘
       │
       ▼
┌──────────────┐
│  /ai/clean   │  ──▶ LLM (Gemini/Groq) cleans & structures scraped data
│              │      into: brand_name, tagline, key_benefits, tone, colors
└──────┬───────┘
       │
       ▼
┌──────────────┐
│ /ai/outline  │  ──▶ LLM writes ad outline: headline, body, CTA,
│              │      keywords, brand voice summary
└──────┬───────┘
       │
       ▼
┌──────────────┐
│ /ai/prompt   │  ──▶ LLM crafts a minimal, brand-aligned image generation
│              │      prompt that captures the brand essence with minimalism
└──────┬───────┘
       │
       ▼
┌──────────────┐
│ /ai/image    │  ──▶ DALL-E 3 / Flux / Stable Diffusion generates image
└──────┬───────┘
       │
       ▼
┌──────────────┐
│ Storage Up   │  ──▶ Download image bytes from provider, upload to
│              │      Supabase Storage bucket `ad-images`, get public URL
└──────┬───────┘
       │
       ▼
┌──────────────┐
│   POST /ads  │  ──▶ Save complete ad (title, description, image_url
│              │      = Supabase Storage URL, keywords, metadata) to DB
└──────┬───────┘
       │
       ▼
   Frontend Editor
   (Rich WYSIWYG — every text line editable, image replaceable)
```

---

## 3. Architecture

```
┌─────────────────────────────────────────────┐
│              DOCKER CONTAINER               │
│  ┌─────────────────────────────────────┐    │
│  │      FastAPI Backend                │    │
│  │  ┌─────────┐ ┌─────────┐ ┌───────┐ │    │
│  │  │  /api   │ │  /api   │ │ /api  │ │    │
│  │  │  /ads   │ │ /scrape │ │ /ai/* │ │    │
│  │  └────┬────┘ └────┬────┘ └───┬───┘ │    │
│  │       │           │          │     │    │
│  │  ┌────┴───────────┴──────────┴───┐ │    │
│  │  │   Supabase JWT Verification   │ │    │
│  │  └───────────────────────────────┘ │    │
│  └─────────────────────────────────────┘    │
└─────────────────────┬───────────────────────┘
                      │
        ┌─────────────┴─────────────┐
        │              SUPABASE                  │
        │  ┌─────────┐ ┌─────────┐ ┌──────────┐  │
        │  │PostgreSQL│ │  Auth   │ │ Storage  │  │
        │  │  ads    │ │(Google/ │ │ad-images │  │
        │  │ profiles│ │  Email) │ │ bucket   │  │
        │  └─────────┘ └─────────┘ └──────────┘  │
        └────────────────────────────────────────┘
                      │
        ┌─────────────┴─────────────────────────────┐
        │           EXTERNAL APIs                   │
        │  ┌─────────────┐  ┌─────────────────────┐ │
        │  │ Cloudflare  │  │   AI Providers      │ │
        │  │   Scraper   │  │  • Gemini (Google)  │ │
        │  └─────────────┘  │  • Groq (Llama)     │ │
        │                   │  • DALL-E 3 (OpenAI)│ │
        │                   │  • Flux / SD        │ │
        │                   │    (Replicate)      │ │
        │                   └─────────────────────┘ │
        └───────────────────────────────────────────┘
```

---

## 4. Authentication Strategy

### 4.1 Delegated Auth (Supabase Auth)
The backend **does not** implement login, signup, password reset, or session management.

| Concern | Implementation |
|---------|---------------|
| **Identity Provider** | Supabase Auth |
| **Supported Methods** | Google OAuth 2.0, Email Magic Link (Passwordless) |
| **Token Format** | Supabase JWT (RS256) |
| **Backend Role** | Verify JWT + extract `sub` (user_id) |

### 4.2 JWT Verification Flow
```
Request ──▶ FastAPI ──▶ Extract Authorization: Bearer <jwt>
                          │
                          ▼
                    Verify with Supabase JWKS
                    (cached, rotated automatically)
                          │
              ┌───────────┴───────────┐
              ▼                       ▼
         Valid JWT               Invalid / Expired
              │                       │
              ▼                       ▼
        Attach user_id           401 Unauthorized
        to request.state
```

### 4.3 Auth Middleware
- **Library:** `supabase-py` or `httpx` hitting Supabase Auth `/user` endpoint.
- **Cache:** JWKS cached in-memory with TTL (e.g., 1 hour).
- **Header:** Every request must include `Authorization: Bearer <supabase_jwt>`.

---

## 5. Database Schema

### 5.1 user_profiles
Supabase Auth manages `auth.users`. This table extends it.

```sql
CREATE TABLE user_profiles (
    id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    email TEXT,
    full_name TEXT,
    avatar_url TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE user_profiles ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can view own profile"
    ON user_profiles FOR SELECT USING (auth.uid() = id);

CREATE POLICY "Users can update own profile"
    ON user_profiles FOR UPDATE USING (auth.uid() = id);
```

### 5.2 ads

```sql
CREATE TABLE ads (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    description TEXT,
    source_url TEXT,
    image_url TEXT,
    image_model TEXT DEFAULT 'dalle3',
    status TEXT DEFAULT 'draft' CHECK (status IN ('draft', 'published', 'archived')),
    -- Pipeline metadata stored as JSONB for flexibility
    metadata JSONB DEFAULT '{
        "scraped_raw": {},
        "cleaned_data": {},
        "outline": {},
        "image_prompt": "",
        "keywords": [],
        "brand_colors": [],
        "tone": ""
    }',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE ads ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can view own ads"
    ON ads FOR SELECT USING (auth.uid() = user_id);

CREATE POLICY "Users can create ads"
    ON ads FOR INSERT WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can update own ads"
    ON ads FOR UPDATE USING (auth.uid() = user_id);

CREATE POLICY "Users can delete own ads"
    ON ads FOR DELETE USING (auth.uid() = user_id);
```

### 5.3 ad_versions (Audit Trail)

```sql
CREATE TABLE ad_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ad_id UUID NOT NULL REFERENCES ads(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    description TEXT,
    image_url TEXT,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE ad_versions ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can view own ad versions"
    ON ad_versions FOR SELECT USING (
        ad_id IN (SELECT id FROM ads WHERE user_id = auth.uid())
    );
```

---

## 6. API Endpoints

### 6.1 Base URL
```
Production:  https://api.adforge.example.com
Development: http://localhost:8000
```

### 6.2 Auth Endpoints (Minimal)

| Method | Endpoint | Description | Auth Required |
|--------|----------|-------------|---------------|
| `GET` | `/api/auth/me` | Returns current user info (reads JWT) | **Yes** |

> **Note:** No `/login`, `/signup`, `/logout`, or `/refresh` endpoints exist in the backend. The frontend negotiates directly with Supabase Auth.

#### GET /api/auth/me
**Response:**
```json
{
  "user_id": "uuid",
  "email": "user@example.com",
  "full_name": "Jane Doe"
}
```

### 6.3 Ad Management

| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| `GET` | `/api/ads` | List all ads for authenticated user | Yes |
| `POST` | `/api/ads` | Create a new ad (manual or from pipeline) | Yes |
| `GET` | `/api/ads/{ad_id}` | Get a specific ad with full metadata | Yes |
| `PUT` | `/api/ads/{ad_id}` | Update an ad (title, description, image_url, metadata) | Yes |
| `DELETE` | `/api/ads/{ad_id}` | Delete an ad | Yes |
| `GET` | `/api/ads/{ad_id}/versions` | Get version history | Yes |

#### POST /api/ads
**Request (from pipeline):**
```json
{
  "title": "Summer Sale 50% Off",
  "description": "Don't miss our biggest sale of the year!",
  "source_url": "https://example.com",
  "image_url": "https://example.com/image.jpg",
  "image_model": "dalle3",
  "metadata": {
    "scraped_raw": { "title": "...", "text": "..." },
    "cleaned_data": { "brand_name": "...", "tone": "..." },
    "outline": { "headline": "...", "body": "...", "cta": "..." },
    "image_prompt": "Minimalist ad for summer sale, clean typography...",
    "keywords": ["sale", "summer"],
    "brand_colors": ["#6366F1", "#10B981"],
    "tone": "playful"
  }
}
```

**Response:**
```json
{
  "id": "uuid",
  "user_id": "uuid",
  "title": "Summer Sale 50% Off",
  "description": "Don't miss our biggest sale of the year!",
  "source_url": "https://example.com",
  "image_url": "https://example.com/image.jpg",
  "image_model": "dalle3",
  "status": "draft",
  "metadata": { ... },
  "created_at": "2024-01-15T10:30:00Z",
  "updated_at": "2024-01-15T10:30:00Z"
}
```

### 6.4 Scraping

| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| `POST` | `/api/scrape` | Scrape a URL via Cloudflare API | Yes |

#### POST /api/scrape
**Request:**
```json
{
  "url": "https://example.com/product-page"
}
```

**Response:**
```json
{
  "success": true,
  "content": {
    "title": "Product Name",
    "description": "Product description from meta tags...",
    "text": "Full extracted text content...",
    "images": ["https://example.com/img1.jpg"],
    "metadata": {
      "og_title": "...",
      "og_description": "...",
      "og_image": "..."
    }
  }
}
```

### 6.5 AI Pipeline Endpoints

| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| `POST` | `/api/ai/clean` | Clean & structure scraped raw content | Yes |
| `POST` | `/api/ai/outline` | Generate ad outline from cleaned data | Yes |
| `POST` | `/api/ai/prompt` | Craft minimal brand-aligned image prompt | Yes |
| `POST` | `/api/ai/generate-image` | Generate image from prompt | Yes |
| `POST` | `/api/ai/generate-text` | Generate ad copy (legacy/all-in-one) | Yes |
| `GET` | `/api/ai/models` | List available AI models | Yes |

#### POST /api/ai/clean
**Request:**
```json
{
  "raw_content": {
    "title": "...",
    "text": "...",
    "metadata": { ... }
  },
  "model": "gemini"
}
```

**Response:**
```json
{
  "brand_name": "Acme Corp",
  "tagline": "Innovation for everyone",
  "key_benefits": ["Fast", "Reliable", "Affordable"],
  "tone": "professional",
  "colors": ["#6366F1", "#111827"],
  "industry": "SaaS",
  "model_used": "gemini-pro"
}
```

#### POST /api/ai/outline
**Request:**
```json
{
  "cleaned_data": {
    "brand_name": "Acme Corp",
    "tone": "professional",
    "key_benefits": ["Fast", "Reliable"]
  },
  "model": "gemini"
}
```

**Response:**
```json
{
  "headline": "Acme Corp: Fast & Reliable Solutions",
  "body": "Experience innovation that moves at your pace...",
  "cta": "Get Started Today",
  "keywords": ["SaaS", "innovation", "reliable"],
  "model_used": "gemini-pro"
}
```

#### POST /api/ai/prompt
**Request:**
```json
{
  "outline": {
    "headline": "Acme Corp: Fast & Reliable Solutions",
    "tone": "professional"
  },
  "cleaned_data": {
    "brand_name": "Acme Corp",
    "colors": ["#6366F1", "#111827"]
  },
  "model": "gemini"
}
```

**Response:**
```json
{
  "prompt": "Minimalist advertisement for Acme Corp, professional SaaS brand, clean white background, subtle indigo (#6366F1) accents, modern sans-serif typography, ample negative space, no clutter, single focal point, high-end editorial style, 4K",
  "model_used": "gemini-pro"
}
```

#### POST /api/ai/generate-image
**Request:**
```json
{
  "prompt": "Minimalist advertisement for Acme Corp...",
  "model": "dalle3",
  "size": "1024x1024"
}
```

**Behavior:**
1. Call the image provider (DALL-E 3 / Flux / SD) with the prompt.
2. Download the returned image bytes server-side.
3. Upload bytes to the **Supabase Storage** bucket `ad-images` at path `{user_id}/{ad_id_or_uuid}.png`.
4. Return the **public Supabase Storage URL** (or signed URL if the bucket is private) as `image_url`. The provider's temporary URL is never returned to the client — only the persisted Supabase URL is, so the image remains viewable during backend testing.

**Response:**
```json
{
  "image_url": "https://<project>.supabase.co/storage/v1/object/public/ad-images/<user_id>/<file>.png",
  "storage_path": "ad-images/<user_id>/<file>.png",
  "model_used": "dalle3",
  "prompt": "Minimalist advertisement for Acme Corp..."
}
```

> **Why:** Provider URLs (DALL-E, Replicate) expire within minutes to hours. Persisting to Supabase Storage gives stable, testable URLs for the lifetime of the ad.

---

## 7. Frontend vs Backend Feature Split

| Feature | Owner | Tool / Tech | Reason |
|---------|-------|-------------|--------|
| **URL Input** | Frontend | React + Tailwind | UI concern |
| **Scraping** | Backend | Cloudflare API | Requires API keys, bypasses protections |
| **AI Cleaning** | Backend | Gemini / Groq | Requires API keys, heavy compute |
| **AI Outline** | Backend | Gemini / Groq | Requires API keys |
| **AI Prompt Crafting** | Backend | Gemini / Groq | Requires API keys |
| **Image Generation** | Backend | DALL-E / Flux / SD | Requires API keys |
| **Ad CRUD** | Backend | FastAPI + Supabase | Data persistence, auth, RLS |
| **Rich Text Editor** | **Frontend** | **TipTap / Slate.js / Quill** (free, open-source) | WYSIWYG editing is a UI concern; free tools exist |
| **Image Editor (crop, replace)** | **Frontend** | **react-image-crop** + HTML5 Canvas (free) | Client-side image manipulation |
| **Inline Text Editing** | **Frontend** | **ContentEditable / TipTap** (free) | Every line editable = frontend UI |
| **Drag-to-reorder elements** | **Frontend** | **React DnD** (free) | UI interaction |
| **Live Preview** | **Frontend** | React state | No backend round-trip needed |
| **Save Edited Ad** | Backend | `PUT /api/ads/{id}` | Persist final edits |

> **Design Principle:** The backend generates the "first draft" ad. The frontend provides a rich, free, open-source editor where the user can tweak every word, swap images, and rearrange elements. Only the final save hits the backend.

---

## 8. Services Layer

### 8.1 Directory Structure

```
backend/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI entry point
│   ├── config.py            # Pydantic-settings config
│   ├── database.py          # SQLAlchemy async engine + session
│   ├── models.py            # SQLAlchemy ORM models
│   ├── schemas.py           # Pydantic request/response models
│   ├── auth.py              # JWT verification middleware ONLY
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── ads.py           # Ad CRUD
│   │   ├── auth.py          # GET /api/auth/me only
│   │   ├── scrape.py        # Cloudflare scraping
│   │   └── ai.py            # Pipeline: clean, outline, prompt, image
│   └── services/
│       ├── __init__.py
│       ├── scraper.py       # Cloudflare API wrapper
│       ├── gemini.py        # Google Gemini service
│       ├── groq.py          # Groq LLM service
│       ├── image_gen.py     # DALL-E / Flux / SD wrapper
│       └── storage.py       # Supabase Storage upload wrapper
├── tests/
│   ├── test_ads.py
│   └── test_ai.py
├── requirements.txt
├── Dockerfile
└── .env.example
```

### 8.2 Service Responsibilities

| Service | File | Responsibility |
|---------|------|---------------|
| **Scraper** | `services/scraper.py` | Calls Cloudflare API to extract raw webpage content, title, meta tags, images. Handles retries. |
| **Gemini** | `services/gemini.py` | Primary LLM for cleaning, outlining, and prompt crafting. |
| **Groq** | `services/groq.py` | Fallback LLM (Llama / Mixtral) for all text generation steps. |
| **ImageGen** | `services/image_gen.py` | Routes image requests to DALL-E 3, Flux Pro, or Stable Diffusion based on `model` param. Returns raw image bytes (not provider URL). |
| **Storage** | `services/storage.py` | Uploads image bytes to Supabase Storage bucket `ad-images` at `{user_id}/{uuid}.png` and returns the persistent public URL. Used by `/api/ai/generate-image` so all generated images are viewable during backend testing without depending on expiring provider URLs. |

---

## 9. Environment Variables

```env
# ── Supabase ──
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your_supabase_anon_key
SUPABASE_SERVICE_KEY=your_supabase_service_role_key
SUPABASE_STORAGE_BUCKET=ad-images

# ── AI Providers ──
GEMINI_API_KEY=your_gemini_api_key
GROQ_API_KEY=your_groq_api_key
OPENAI_API_KEY=your_openai_api_key
REPLICATE_API_KEY=your_replicate_api_key

# ── Cloudflare Scraping ──
CLOUDFLARE_API_KEY=your_cloudflare_api_key
CLOUDFLARE_ACCOUNT_ID=your_cloudflare_account_id

# ── App Config ──
APP_ENV=development
FRONTEND_URL=http://localhost:3000
SECRET_KEY=your_jwt_secret_key
```

---

## 10. Dependencies

```
fastapi==0.109.0
uvicorn[standard]==0.27.0
sqlalchemy[asyncio]==2.0.25
asyncpg==0.29.0
httpx==0.26.0
python-jose[cryptography]==3.3.0
python-multipart==0.0.6
pydantic==2.5.3
pydantic-settings==2.1.0
python-dotenv==1.0.0
supabase==2.3.0
```

---

## 11. Risk Assessment

| Risk | Severity | Mitigation |
|------|----------|------------|
| AI API rate limits | High | Implement in-memory caching + fallback models |
| Cloudflare scraping fails | Medium | Exponential backoff; return 502 with retry-after |
| Supabase Auth downtime | Low | Supabase 99.9% SLA; frontend handles re-auth |
| JWT expiry | Low | Frontend auto-refreshes via Supabase client |
| Image storage growth | Medium | Store only URLs; purge unused ad images periodically |
| Pipeline latency (4 AI calls) | High | Run steps sequentially but stream progress to frontend via SSE or polling |

---

## 12. Success Metrics (Backend)

| Metric | Target | Measurement |
|--------|--------|-------------|
| API Uptime | 99.9% | Uptime monitor on `/health` |
| Avg Response Time | < 2s | Middleware latency logging |
| Error Rate | < 1% | 5xx responses / total requests |
| Scraping Success Rate | > 95% | Successful `/api/scrape` calls |
| AI Generation Success | > 98% | Successful `/api/ai/*` calls |
| Pipeline Completion | > 90% | Full scrape→image flow succeeds |

---

## 13. Deployment Checklist

- [ ] Docker + Docker Compose installed on host
- [ ] Supabase project created (Auth configured for Google + Email Magic Link)
- [ ] PostgreSQL tables migrated (`user_profiles`, `ads`, `ad_versions`)
- [ ] RLS policies applied
- [ ] Supabase Storage bucket `ad-images` created (public read, authenticated write) so generated images render in the browser during testing
- [ ] `.env` populated with all API keys
- [ ] Docker image built: `docker build -t adforge-backend .`
- [ ] Container running with `docker run -p 8000:8000`
- [ ] Health check endpoint (`GET /health`) responding 200
- [ ] Reverse proxy + SSL configured (Nginx / Caddy / Traefik)

---

**Document Version:** 3.0 — Backend Only  
**Last Updated:** 2026-05-12  
**Status:** Draft
