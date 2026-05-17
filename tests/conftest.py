"""Shared pytest fixtures.

Provides:
* ``client`` — sync TestClient without auth override (for 401 tests)
* ``auth_client`` — sync TestClient with ``get_current_user`` overridden
* Service mocks (scraper, gemini, groq, image_gen, storage) via ``unittest.mock.patch``
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from backend.auth import CurrentUser
from backend.database import get_db
from backend.main import create_app
from backend.models import Ad, AdVersion

# ── Model helpers ──────────────────────────────────────────────────────────────


def _now() -> datetime:
    return datetime.now(UTC)


def make_ad(**overrides: Any) -> Ad:
    kwargs: dict[str, Any] = {
        "id": uuid4(),
        "user_id": UUID("11111111-1111-4111-8111-111111111111"),
        "title": "Test Ad",
        "description": "A test ad",
        "source_url": None,
        "image_url": None,
        "image_model": "flux",
        "status": "draft",
        "meta": {},
        "created_at": _now(),
        "updated_at": _now(),
    }
    kwargs.update(overrides)
    return Ad(**kwargs)


# ── Fake DB ────────────────────────────────────────────────────────────────────


class _FakeSession:
    """In-memory mock of an async SQLAlchemy session.

    ``add`` queues objects, ``flush`` assigns server-side defaults (UUID,
    timestamps) and moves them to storage, ``refresh`` is a no-op.
    ``execute`` returns all stored instances matching the table being queried.
    """

    def __init__(self) -> None:
        self._ads: dict[UUID, Ad] = {}
        self._versions: dict[UUID, AdVersion] = {}
        self._pending: list[Any] = []

    async def execute(self, statement: Any) -> _FakeResult:
        return self._execute(statement)

    def _execute(self, statement: Any) -> _FakeResult:
        from sqlalchemy.sql.selectable import Select

        if not isinstance(statement, Select):
            return _FakeResult()
        tables = [f.name for f in statement.get_final_froms() if hasattr(f, "name")]
        if "ad_versions" in tables:
            rows = sorted(
                self._versions.values(), key=lambda v: v.created_at or datetime.min, reverse=True
            )
            return _FakeResult(list(rows))
        if "ads" in tables:
            rows = sorted(
                self._ads.values(), key=lambda a: a.created_at or datetime.min, reverse=True
            )
            return _FakeResult(list(rows))
        return _FakeResult()

    def _defaults(self, obj: Any) -> None:
        now = datetime.now(UTC)
        if isinstance(obj, Ad):
            if obj.id is None:
                obj.id = uuid4()
            if obj.created_at is None:
                obj.created_at = now
            obj.updated_at = now
        elif isinstance(obj, AdVersion):
            if obj.id is None:
                obj.id = uuid4()
            if obj.created_at is None:
                obj.created_at = now

    def add(self, obj: Any) -> None:
        self._defaults(obj)
        self._pending.append(obj)

    async def flush(self) -> None:
        for obj in self._pending:
            if isinstance(obj, Ad):
                self._ads[obj.id] = obj
            elif isinstance(obj, AdVersion):
                self._versions[obj.id] = obj
        self._pending.clear()

    async def refresh(self, obj: Any) -> None:
        pass

    async def scalar(self, statement: Any) -> Any:
        result = self._execute(statement)
        row = result.scalar_one_or_none()
        if isinstance(row, (Ad, AdVersion)):
            return None
        return row

    async def delete(self, obj: Any) -> None:
        if isinstance(obj, Ad) and obj.id in self._ads:
            del self._ads[obj.id]
            self._versions = {k: v for k, v in self._versions.items() if v.ad_id != obj.id}

    async def close(self) -> None:
        pass

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass


class _FakeResult:
    def __init__(self, rows: list | None = None) -> None:
        self._rows = rows or []

    def scalar_one_or_none(self) -> Any:
        return self._rows[0] if self._rows else None

    def scalars(self) -> _FakeResult:
        return self

    def all(self) -> list[Any]:
        return self._rows


# ── Constants ──────────────────────────────────────────────────────────────────


_FAKE_USER = CurrentUser(
    id=UUID("11111111-1111-4111-8111-111111111111"),
    email="test@example.com",
    role="authenticated",
    claims={
        "sub": "11111111-1111-4111-8111-111111111111",
        "email": "test@example.com",
        "role": "authenticated",
    },
)


# ── App fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def app() -> FastAPI:
    return create_app()


@pytest.fixture
def db_session() -> _FakeSession:
    return _FakeSession()


@pytest.fixture
def client(app: FastAPI, db_session: _FakeSession) -> TestClient:
    """Client with NO auth override — protected endpoints return 401."""
    app.dependency_overrides[get_db] = lambda: db_session
    return TestClient(app)


@pytest.fixture
def auth_client(app: FastAPI, db_session: _FakeSession) -> TestClient:
    """Client with ``get_current_user`` overridden — all requests authenticated."""
    from backend.auth import get_current_user

    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: _FAKE_USER
    return TestClient(app)


@pytest.fixture
def headers() -> dict[str, str]:
    return {"Authorization": "Bearer fake-token"}


# ── Service mock fixtures (patch modules used in routers) ──────────────────────


@pytest.fixture
def mock_scraper() -> None:
    with patch("backend.services.scraper.scrape", new_callable=AsyncMock) as m:
        m.return_value = MagicMock(
            title="Test Title",
            description="Test description",
            text="Test body text",
            images=[],
            metadata={"source_url": "https://example.com/"},
        )
        yield


_MOCK_CLEAN_RESPONSE = {
    "brand_name": "TestBrand",
    "tagline": "We test",
    "key_benefits": ["Speed", "Quality"],
    "tone": "Professional",
    "colors": ["#000", "#fff"],
    "industry": "Tech",
    "model_used": "gemini-2.5-flash",
}

_MOCK_OUTLINE_RESPONSE = {
    "headline": "Test Headline",
    "body": "Test body copy",
    "cta": "Get Started",
    "keywords": ["test", "demo"],
    "model_used": "gemini-2.5-flash",
}

_MOCK_PROMPT_RESPONSE = {
    "prompt": "A test image prompt with clean background",
    "model_used": "gemini-2.5-flash",
}


@pytest.fixture
def mock_gemini() -> None:
    with (
        patch("backend.services.gemini.clean", new_callable=AsyncMock) as clean,
        patch("backend.services.gemini.outline", new_callable=AsyncMock) as outline,
        patch("backend.services.gemini.craft_prompt", new_callable=AsyncMock) as prompt,
    ):
        clean.return_value = dict(_MOCK_CLEAN_RESPONSE)
        outline.return_value = dict(_MOCK_OUTLINE_RESPONSE)
        prompt.return_value = dict(_MOCK_PROMPT_RESPONSE)
        yield


_MOCK_GROQ_CLEAN = {
    "brand_name": "GroqBrand",
    "tagline": "Fast inference",
    "key_benefits": ["Speed"],
    "tone": "Technical",
    "colors": [],
    "industry": "AI",
    "model_used": "llama-3.3-70b-versatile",
}

_MOCK_GROQ_OUTLINE = {
    "headline": "Groq Headline",
    "body": "Groq body",
    "cta": "Try Groq",
    "keywords": ["groq", "llama"],
    "model_used": "llama-3.3-70b-versatile",
}

_MOCK_GROQ_PROMPT = {
    "prompt": "A Groq-themed image",
    "model_used": "llama-3.3-70b-versatile",
}


@pytest.fixture
def mock_groq() -> None:
    with (
        patch("backend.services.groq.clean", new_callable=AsyncMock) as clean,
        patch("backend.services.groq.outline", new_callable=AsyncMock) as outline,
        patch("backend.services.groq.craft_prompt", new_callable=AsyncMock) as prompt,
    ):
        clean.return_value = dict(_MOCK_GROQ_CLEAN)
        outline.return_value = dict(_MOCK_GROQ_OUTLINE)
        prompt.return_value = dict(_MOCK_GROQ_PROMPT)
        yield


@pytest.fixture
def mock_image_gen() -> None:
    with patch("backend.services.image_gen.generate", new_callable=AsyncMock) as m:
        m.return_value = b"fake-image-bytes"
        yield


@pytest.fixture
def mock_storage() -> None:
    with patch("backend.services.storage.upload_image", new_callable=AsyncMock) as m:
        m.return_value = MagicMock(
            url="https://storage.supabase.co/test/image.jpg",
            path="test/image.jpg",
        )
        yield m
