"""Ads CRUD router — ``/api/ads`` and ``/api/ads/{ad_id}/versions``.

Authorisation
-------------
The backend connects to Postgres as the **service role**, which bypasses RLS.
That means we cannot rely on Supabase RLS as the final access control here —
the router itself enforces per-user scoping by filtering every query on
``Ad.user_id == current_user.id``. RLS still applies for any direct REST /
Dashboard access; this layer mirrors it for the API surface.

Version snapshots
-----------------
On every successful ``PUT /api/ads/{id}`` we write the *prior* state of the
ad into ``ad_versions`` before applying the patch. ``POST /api/ads`` also
writes an initial snapshot so callers can always rewind to "as created".
The snapshot fields are intentionally narrow (title, description, image_url,
metadata) — they cover what humans care about during editing without
duplicating immutable rows like ``user_id``.

Idempotency
-----------
``POST /api/ads`` accepts an optional ``Idempotency-Key`` header. When
present, the response (status + body) is cached for ``idempotency_ttl``
seconds so that retries do not create duplicate ads.
"""

from __future__ import annotations

import time
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth import CurrentUser, get_current_user
from backend.config import get_settings
from backend.database import get_db
from backend.models import Ad, AdVersion
from backend.schemas import (
    AdCreate,
    AdListResponse,
    AdResponse,
    AdUpdate,
    AdVersionListResponse,
    AdVersionResponse,
)

router = APIRouter(prefix="/api/ads", tags=["ads"])


# ── Idempotency cache ─────────────────────────────────────────────────


class _IdempotencyCache:
    """In-memory idempotency-key store with TTL.

    Production deployments should replace this with Redis; the in-memory
    variant is correct for single-worker deployments and development.
    """

    def __init__(self) -> None:
        self._store: dict[str, tuple[float, int, dict[str, object]]] = {}

    def _ttl(self) -> int:
        return get_settings().idempotency_ttl

    def get(self, key: str) -> tuple[int, dict[str, object]] | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        expires_at, status_code, body = entry
        if time.monotonic() > expires_at:
            del self._store[key]
            return None
        return status_code, body

    def set(self, key: str, status_code: int, body: dict[str, object]) -> None:
        self._store[key] = (time.monotonic() + self._ttl(), status_code, body)


_idempotency = _IdempotencyCache()


# ── Helpers ───────────────────────────────────────────────────────────


async def _get_owned_ad(
    ad_id: UUID,
    user: CurrentUser,
    db: AsyncSession,
) -> Ad:
    stmt = select(Ad).where(Ad.id == ad_id, Ad.user_id == user.id)
    result = await db.execute(stmt)
    ad = result.scalar_one_or_none()
    if ad is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Ad {ad_id} not found.",
        )
    return ad


def _snapshot(ad: Ad) -> AdVersion:
    return AdVersion(
        ad_id=ad.id,
        title=ad.title,
        description=ad.description,
        image_url=ad.image_url,
        meta=dict(ad.meta),
    )


# ── List (paginated) ──────────────────────────────────────────────────


@router.get("", response_model=AdListResponse)
async def list_ads(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    cursor: UUID | None = Query(
        None,
        description="UUID of the last ad from the previous page for cursor pagination.",
    ),
    limit: int = Query(
        default=20,
        ge=1,
        le=100,
        description="Maximum number of ads to return (1-100).",
    ),
) -> AdListResponse:
    stmt = select(Ad).where(Ad.user_id == user.id)

    if cursor is not None:
        cursor_stmt = select(Ad.created_at).where(Ad.id == cursor, Ad.user_id == user.id)
        cursor_ts = await db.scalar(cursor_stmt)
        if cursor_ts is not None:
            stmt = stmt.where(
                (Ad.created_at < cursor_ts) | ((Ad.created_at == cursor_ts) & (Ad.id < cursor))
            )

    stmt = stmt.order_by(Ad.created_at.desc(), Ad.id.desc()).limit(limit + 1)
    result = await db.execute(stmt)
    rows = list(result.scalars().all())

    has_more = len(rows) > limit
    if has_more:
        rows = rows[:limit]

    items = [AdResponse.model_validate(ad) for ad in rows]
    next_cursor = str(rows[-1].id) if rows and has_more else None
    return AdListResponse(items=items, count=len(items), next_cursor=next_cursor)


# ── Create (idempotent) ───────────────────────────────────────────────


@router.post("", response_model=AdResponse, status_code=status.HTTP_201_CREATED)
async def create_ad(
    body: AdCreate,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> AdResponse:
    if idempotency_key is not None:
        cached = _idempotency.get(idempotency_key)
        if cached is not None:
            cached_status, cached_data = cached
            return JSONResponse(  # type: ignore[return-value]
                status_code=cached_status,
                content=cached_data,
                headers={"Idempotency-Replay": "true"},
            )

    ad = Ad(
        user_id=user.id,
        title=body.title,
        description=body.description,
        source_url=str(body.source_url) if body.source_url else None,
        image_url=str(body.image_url) if body.image_url else None,
        image_model=body.image_model,
        status=body.status,
        meta=body.metadata,
    )
    db.add(ad)
    await db.flush()
    db.add(_snapshot(ad))
    await db.flush()
    await db.refresh(ad)

    response = AdResponse.model_validate(ad)

    if idempotency_key is not None:
        _idempotency.set(idempotency_key, 201, response.model_dump(mode="json"))

    return response


# ── Get one ───────────────────────────────────────────────────────────


@router.get("/{ad_id}", response_model=AdResponse)
async def get_ad(
    ad_id: UUID,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AdResponse:
    ad = await _get_owned_ad(ad_id, user, db)
    return AdResponse.model_validate(ad)


# ── Update ────────────────────────────────────────────────────────────


@router.put("/{ad_id}", response_model=AdResponse)
async def update_ad(
    ad_id: UUID,
    body: AdUpdate,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AdResponse:
    ad = await _get_owned_ad(ad_id, user, db)
    db.add(_snapshot(ad))

    patch = body.model_dump(exclude_unset=True)
    if "source_url" in patch and patch["source_url"] is not None:
        patch["source_url"] = str(patch["source_url"])
    if "image_url" in patch and patch["image_url"] is not None:
        patch["image_url"] = str(patch["image_url"])
    if "metadata" in patch:
        patch["meta"] = patch.pop("metadata")
    for key, value in patch.items():
        setattr(ad, key, value)

    await db.flush()
    await db.refresh(ad)
    return AdResponse.model_validate(ad)


# ── Delete ────────────────────────────────────────────────────────────


@router.delete("/{ad_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_ad(
    ad_id: UUID,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    ad = await _get_owned_ad(ad_id, user, db)
    await db.delete(ad)


# ── Versions ──────────────────────────────────────────────────────────


@router.get("/{ad_id}/versions", response_model=AdVersionListResponse)
async def list_versions(
    ad_id: UUID,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AdVersionListResponse:
    await _get_owned_ad(ad_id, user, db)
    stmt = select(AdVersion).where(AdVersion.ad_id == ad_id).order_by(AdVersion.created_at.desc())
    result = await db.execute(stmt)
    versions = result.scalars().all()
    items = [AdVersionResponse.model_validate(v) for v in versions]
    return AdVersionListResponse(items=items, count=len(items))
