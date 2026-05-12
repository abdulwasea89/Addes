"""AI router — clean / outline / prompt / generate-image / generate-text / models.

Implemented in Phase 8.3.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/ai", tags=["ai"])
