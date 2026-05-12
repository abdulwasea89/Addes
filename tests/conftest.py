"""Shared pytest fixtures.

Phase 11 fills this in with: a TestClient bound to ``backend.main:app``, a JWT
factory that mints Supabase-shaped tokens for protected-route tests, and
mocks for external HTTP services (scraper, LLMs, image providers, storage).
"""

from __future__ import annotations
