"""Ads router — CRUD + version history.

Implemented in Phase 8.4.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/ads", tags=["ads"])
