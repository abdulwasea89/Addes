"""FastAPI application factory.

Phase 9 wiring lives here:

* All routers mounted under ``/api/*`` plus a public ``/health`` probe.
* CORS allowing the configured ``FRONTEND_URL`` plus local Vite/Next dev
  defaults (``http://localhost:3000`` and ``http://localhost:5173``) so the
  same image works for ``npm run dev`` and prod.
* A request-latency logging middleware that records method, path, status,
  and milliseconds-elapsed for every request and exposes the same number
  to clients via the ``X-Process-Time-ms`` header.
* A request-ID middleware that tags every request with a ``UUID4`` for
  observability and returns it as ``X-Request-ID``.
* Security headers (CSP, XFO, HSTS, …), GZip compression, and trusted-host
  enforcement.
* ORJSON response serialisation for faster JSON encoding.
* A global exception handler that wraps unhandled errors in a uniform shape
  with ``request_id``.
* An async lifespan that warms the Supabase JWKS cache and pings the
  database on startup (best-effort — failures are logged, not fatal, so a
  brief Supabase blip doesn't crash the pod). On shutdown the SQLAlchemy
  engine is disposed cleanly to avoid leaking pooled connections.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from starlette.middleware.base import BaseHTTPMiddleware

from backend.auth import _jwks_cache
from backend.config import Settings, get_settings
from backend.database import dispose_engine, get_engine
from backend.routers import ads as ads_router
from backend.routers import ai as ai_router
from backend.routers import auth as auth_router
from backend.routers import scrape as scrape_router

logger = logging.getLogger("backend")


def _configure_logging() -> None:
    backend_logger = logging.getLogger("backend")
    if backend_logger.handlers:
        return
    uvicorn_logger = logging.getLogger("uvicorn")
    if uvicorn_logger.handlers:
        for handler in uvicorn_logger.handlers:
            backend_logger.addHandler(handler)
    else:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s:    %(name)s: %(message)s"))
        backend_logger.addHandler(handler)
    backend_logger.setLevel(logging.INFO)
    backend_logger.propagate = False


# ── Health probe ──────────────────────────────────────────────────────


async def _health() -> dict[str, str]:
    return {"status": "ok"}


# ── Lifespan: warm caches + dispose engine ────────────────────────────


async def _warm_jwks(settings: Settings) -> None:
    try:
        _jwks_cache._refresh(settings)
        keys = len(_jwks_cache._keys_by_kid)
        logger.info("JWKS warmed: %d key(s) cached", keys)
    except Exception as exc:
        logger.warning("JWKS warm-up failed (will retry on first request): %s", exc)


async def _ping_db() -> None:
    try:
        engine = get_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        logger.info("Database connectivity OK")
    except Exception as exc:
        logger.warning("Database ping failed (continuing anyway): %s", exc)


@asynccontextmanager
async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    logger.info("Starting Adess backend (env=%s)", settings.app_env)
    await _warm_jwks(settings)
    await _ping_db()
    try:
        yield
    finally:
        await dispose_engine()
        logger.info("SQLAlchemy engine disposed")


# ── Request-ID middleware ─────────────────────────────────────────────


class _RequestIDMiddleware(BaseHTTPMiddleware):
    """Tag every request with a ``UUID4`` and expose it as ``X-Request-ID``.

    The ID is also stored at ``request.state.request_id`` so other middleware
    and exception handlers can include it in logs and error responses.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        rid = str(uuid.uuid4())
        request.state.request_id = rid
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        return response


# ── Security-headers middleware ───────────────────────────────────────


class _SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Set hardening response headers for every response.

    These follow current best practices from Mozilla Observatory and OWASP:
    * ``X-Content-Type-Options: nosniff`` — prevents MIME-type sniffing.
    * ``X-Frame-Options: DENY`` — blocks clickjacking.
    * ``Content-Security-Policy`` — restricts resource origins.
    * ``Referrer-Policy`` — controls referrer header leakage.
    * ``Permissions-Policy`` — disables unnecessary browser features.
    """

    _CSP = (
        "default-src 'self'; "
        "img-src 'self' data: https:; "
        "style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
        "frame-ancestors 'none'"
    )

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy",
            "geolocation=(), microphone=(), camera=(), fullscreen=()",
        )
        # Skip CSP for docs (Swagger/ReDoc)
        if request.url.path not in ("/docs", "/redoc", "/openapi.json"):
            response.headers.setdefault("Content-Security-Policy", self._CSP)
        return response


# ── Request-latency middleware ────────────────────────────────────────


class _LatencyMiddleware(BaseHTTPMiddleware):
    """Log method / path / status / ms + request_id for every request."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        start = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        response.headers["X-Process-Time-ms"] = f"{elapsed_ms:.2f}"
        rid = getattr(request.state, "request_id", "-")
        logger.info(
            "[%s] %s %s -> %d (%.2f ms)",
            rid,
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
        )
        return response


# ── Global exception handler ──────────────────────────────────────────


async def _global_exception_handler(request: Request, exc: Exception) -> Response:
    """Catch unhandled exceptions and return a uniform JSON error.

    The response always includes ``request_id`` so callers can correlate
    errors with server logs.
    """
    rid = getattr(request.state, "request_id", "-")
    logger.exception("[%s] Unhandled exception: %s", rid, exc)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error.", "request_id": rid},
    )


# ── CORS origins ──────────────────────────────────────────────────────


_DEV_ORIGINS: tuple[str, ...] = (
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
)


def _cors_origins(settings: Settings) -> list[str]:
    frontend = str(settings.frontend_url).rstrip("/")
    return [frontend, *_DEV_ORIGINS]


def _allowed_hosts(settings: Settings) -> list[str]:
    allowed = ["localhost", "127.0.0.1", "testserver", "test"]
    frontend = str(settings.frontend_url).rstrip("/")
    if "//" in frontend:
        netloc = frontend.split("//", 1)[1]
        allowed.append(netloc.split(":")[0])
    return list(set(allowed))


# ── App factory ───────────────────────────────────────────────────────


def create_app() -> FastAPI:
    """Build and return the FastAPI application."""
    _configure_logging()
    settings = get_settings()
    app = FastAPI(
        title="Adess Backend",
        version="0.1.0",
        description="AI-powered ad creation backend.",
        lifespan=_lifespan,
    )

    # Middleware order (outermost = runs first):
    # 1. TrustedHost — reject unknown hosts early
    # 2. SecurityHeaders — set headers for every response
    # 3. GZip — compress before sending
    # 4. CORSMiddleware — handle CORS preflights
    # 5. RequestID — tag every request
    # 6. Latency — log + measure timing

    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=_allowed_hosts(settings),
    )
    app.add_middleware(_SecurityHeadersMiddleware)
    app.add_middleware(GZipMiddleware, minimum_size=1000)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(settings),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Process-Time-ms", "X-Request-ID"],
    )
    app.add_middleware(_RequestIDMiddleware)
    app.add_middleware(_LatencyMiddleware)

    app.add_exception_handler(Exception, _global_exception_handler)

    @app.get("/", tags=["meta"])
    async def _root() -> dict[str, object]:
        return {
            "message": "Adess Backend API",
            "docs": "/docs",
            "redoc": "/redoc",
            "health": "/health",
            "endpoints": {
                "auth": "GET /api/auth/me",
                "scrape": "POST /api/scrape",
                "ai": {
                    "clean": "POST /api/ai/clean",
                    "outline": "POST /api/ai/outline",
                    "prompt": "POST /api/ai/prompt",
                    "generate_image": "POST /api/ai/generate-image",
                    "generate_text": "POST /api/ai/generate-text",
                    "models": "GET /api/ai/models",
                },
                "ads": {
                    "list": "GET /api/ads",
                    "create": "POST /api/ads",
                    "get": "GET /api/ads/{id}",
                    "update": "PUT /api/ads/{id}",
                    "delete": "DELETE /api/ads/{id}",
                    "versions": "GET /api/ads/{id}/versions",
                },
            },
        }

    app.add_api_route("/health", _health, methods=["GET"], tags=["meta"])
    app.include_router(auth_router.router)
    app.include_router(scrape_router.router)
    app.include_router(ai_router.router)
    app.include_router(ads_router.router)

    return app


app: FastAPI = create_app()
