"""Image generation service — DALL-E 3 / Flux / Stable Diffusion.

Returns raw image bytes (not the provider URL) so the caller can persist them
to Supabase Storage via :mod:`backend.services.storage`.

Implemented in Phase 7.4.
"""

from __future__ import annotations
