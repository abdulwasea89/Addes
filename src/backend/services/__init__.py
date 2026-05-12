"""External-service wrappers.

Each submodule isolates a single third-party concern so callers can swap or
mock providers without touching router code:

- :mod:`backend.services.scraper`   — Cloudflare scraping API
- :mod:`backend.services.gemini`    — Google Gemini LLM
- :mod:`backend.services.groq`      — Groq LLM (Llama/Mixtral) fallback
- :mod:`backend.services.image_gen` — DALL-E / Flux / Stable Diffusion
- :mod:`backend.services.storage`   — Supabase Storage uploads
"""

from __future__ import annotations
