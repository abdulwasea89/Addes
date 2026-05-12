"""Scrape router — ``POST /api/scrape``.

Implemented in Phase 8.2.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/scrape", tags=["scrape"])
