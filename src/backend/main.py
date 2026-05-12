"""FastAPI application factory.

Routers and middleware are mounted here. Full wiring (CORS, request logging,
lifespan hooks for DB engine disposal) lands in Phase 9 — for now we expose
the auth router so Phase 5 verification can hit a protected endpoint.
"""

from __future__ import annotations

from fastapi import FastAPI

from backend.routers import auth as auth_router


async def _health() -> dict[str, str]:
    return {"status": "ok"}


def create_app() -> FastAPI:
    """Build and return the FastAPI application."""
    app = FastAPI(
        title="Adess Backend",
        version="0.1.0",
        description="AI-powered ad creation backend.",
    )
    app.add_api_route("/health", _health, methods=["GET"], tags=["meta"])
    app.include_router(auth_router.router)
    return app


app: FastAPI = create_app()
