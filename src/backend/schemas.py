"""Pydantic v2 request/response schemas for every router.

Conventions used across this module:

* **Strict by default.** Schemas extend :class:`_Strict`, which forbids unknown
  fields and strips whitespace from strings. This catches typos in client
  payloads early and keeps the OpenAPI doc honest.
* **Field validation in the schema, not the router.** ``HttpUrl``,
  ``min_length``, ``MIN/MAX`` constants etc. live here, so every endpoint
  inherits the same input rules without duplication.
* **Response models mirror the DB.** ``AdResponse``/``AdVersionResponse`` use
  ``from_attributes=True`` so we can return ORM rows directly. The JSONB
  ``meta`` attribute on the ORM maps onto a ``metadata`` field in the response
  via :class:`AliasChoices`, matching the public API contract documented in
  ``BACKEND_SPEC.md`` §6.3 while keeping the ORM attribute name unambiguous.
* **Pipeline payloads are loose JSON.** ``raw_content``, ``cleaned_data``, and
  ``outline`` are dict-typed because their shape is owned by the LLM step that
  produced them; locking them down here would force a schema change every time
  a model adds a field.

The schemas are intentionally small and dependency-free — they describe the
wire format and nothing more. Business rules live in the routers and services.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
)

# ── Shared constants ───────────────────────────────────────────────────

#: Allowed values for ``Ad.status`` — kept in sync with the
#: ``ads_status_check`` constraint in ``sql/schema.sql``.
AdStatus = Literal["draft", "published", "archived"]

#: Allowed LLM providers for the AI pipeline endpoints. Adding a provider
#: means wiring a service module in ``backend.services`` and extending this
#: literal — the routers will then accept it automatically.
LLMModel = Literal["gemini", "groq"]

#: Allowed image generation providers. See ``services/image_gen.py`` for
#: routing.
#:
#: Recommendations (May 2026 catalog):
#: * ``pollinations``       — **default**. Free, no signup, FLUX-tier quality.
#: * ``pollinations-turbo`` — Pollinations' fast variant, lower quality.
#: * ``flux``               — Cloudflare FLUX.1 schnell, ~3-5 s.
#: * ``sdxl-lightning``     — Cloudflare ByteDance SDXL Lightning, 2-step.
#: * ``dreamshaper``        — Cloudflare DreamShaper 8 LCM, photorealistic.
#: * ``sd``                 — Cloudflare SDXL base 1.0, legacy.
ImageModel = Literal[
    "pollinations",
    "pollinations-turbo",
    "flux",
    "sdxl-lightning",
    "dreamshaper",
    "sd",
    "dalle3",
]

#: Whitelisted DALL-E / Replicate sizes. Keeping this as a literal means
#: invalid sizes get rejected at validation time rather than burning provider
#: quota on a 400.
ImageSize = Literal["1024x1024", "1024x1792", "1792x1024", "512x512"]


# ── Base classes ───────────────────────────────────────────────────────


class _Strict(BaseModel):
    """Common base: forbid extras, strip whitespace, enable enum-by-value."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        use_enum_values=True,
    )


class _ORM(_Strict):
    """Response base for models that read from SQLAlchemy ORM rows."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        use_enum_values=True,
        from_attributes=True,
    )


# ── Auth ───────────────────────────────────────────────────────────────


class UserMe(_Strict):
    """Body of ``GET /api/auth/me``."""

    user_id: UUID
    email: str | None = None
    full_name: str | None = None
    role: str = "authenticated"


# ── Ad CRUD ────────────────────────────────────────────────────────────


class AdCreate(_Strict):
    """Body of ``POST /api/ads``.

    ``user_id`` is **not** accepted from the client — it's taken from the JWT
    inside the router. The request only carries content the user actually
    authored or generated.
    """

    title: str = Field(min_length=1, max_length=512)
    description: str | None = None
    source_url: HttpUrl | None = None
    image_url: HttpUrl | None = None
    image_model: str = Field(default="pollinations", max_length=64)
    status: AdStatus = "draft"
    metadata: dict[str, Any] = Field(default_factory=dict)


class AdUpdate(_Strict):
    """Body of ``PUT /api/ads/{ad_id}`` — every field is optional.

    Routers should apply only the fields present in the model dump so that
    ``None`` doesn't accidentally wipe columns.
    """

    title: str | None = Field(default=None, min_length=1, max_length=512)
    description: str | None = None
    source_url: HttpUrl | None = None
    image_url: HttpUrl | None = None
    image_model: str | None = Field(default=None, max_length=64)
    status: AdStatus | None = None
    metadata: dict[str, Any] | None = None


class AdResponse(_ORM):
    """Single ad row as returned by every read endpoint.

    ``metadata`` maps to the ORM attribute :pyattr:`backend.models.Ad.meta`
    (the column itself is named ``metadata`` in Postgres but renamed in the
    ORM to avoid clashing with SQLAlchemy's declarative ``metadata``).
    """

    id: UUID
    user_id: UUID
    title: str
    description: str | None
    source_url: str | None
    image_url: str | None
    image_model: str
    status: AdStatus
    metadata: dict[str, Any] = Field(
        # Try the ORM attribute name (``meta``) first — the ``metadata`` name
        # also exists on every SQLAlchemy declarative class as the shared
        # ``MetaData()`` registry, and Pydantic would happily pick it up,
        # blowing up validation with ``input should be a valid dictionary``.
        validation_alias=AliasChoices("meta", "metadata"),
        serialization_alias="metadata",
    )
    created_at: datetime
    updated_at: datetime


class AdListResponse(_Strict):
    """Body of ``GET /api/ads``.

    Pagination is cursor-based: ``next_cursor`` is the ``id`` of the last item
    in the current page. Pass it as ``?cursor=...`` on the next request to
    fetch the subsequent page. ``null`` means there are no more results.
    """

    items: list[AdResponse]
    count: int = Field(ge=0)
    next_cursor: str | None = None


class AdVersionResponse(_ORM):
    """Single snapshot row from ``ad_versions``."""

    id: UUID
    ad_id: UUID
    title: str
    description: str | None
    image_url: str | None
    metadata: dict[str, Any] = Field(
        # Try the ORM attribute name (``meta``) first — the ``metadata`` name
        # also exists on every SQLAlchemy declarative class as the shared
        # ``MetaData()`` registry, and Pydantic would happily pick it up,
        # blowing up validation with ``input should be a valid dictionary``.
        validation_alias=AliasChoices("meta", "metadata"),
        serialization_alias="metadata",
    )
    created_at: datetime


class AdVersionListResponse(_Strict):
    """Body of ``GET /api/ads/{ad_id}/versions``."""

    items: list[AdVersionResponse]
    count: int = Field(ge=0)


# ── Scrape ─────────────────────────────────────────────────────────────


class ScrapeRequest(_Strict):
    """Body of ``POST /api/scrape``."""

    url: HttpUrl


class ScrapedContent(_Strict):
    """The ``content`` block returned by the scraper service."""

    title: str | None = None
    description: str | None = None
    text: str = ""
    images: list[HttpUrl] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ScrapeResponse(_Strict):
    """Body of the scrape endpoint — matches BACKEND_SPEC §6.4."""

    success: bool = True
    content: ScrapedContent


# ── AI pipeline ────────────────────────────────────────────────────────


class CleanRequest(_Strict):
    raw_content: dict[str, Any]
    model: LLMModel = "gemini"


class CleanResponse(_Strict):
    brand_name: str | None = None
    tagline: str | None = None
    key_benefits: list[str] = Field(default_factory=list)
    tone: str | None = None
    colors: list[str] = Field(default_factory=list)
    industry: str | None = None
    model_used: str


class OutlineRequest(_Strict):
    cleaned_data: dict[str, Any]
    model: LLMModel = "gemini"


class OutlineResponse(_Strict):
    headline: str
    body: str
    cta: str
    keywords: list[str] = Field(default_factory=list)
    model_used: str


class PromptRequest(_Strict):
    outline: dict[str, Any]
    cleaned_data: dict[str, Any]
    model: LLMModel = "gemini"


class PromptResponse(_Strict):
    prompt: str = Field(min_length=1)
    model_used: str


class ImageRequest(_Strict):
    prompt: str = Field(min_length=1, max_length=4000)
    model: ImageModel = "pollinations"
    size: ImageSize = "1024x1024"


class ImageResponse(_Strict):
    image_url: HttpUrl
    storage_path: str
    model_used: str
    prompt: str


class TextGenRequest(_Strict):
    """Legacy all-in-one copy generator — kept for backwards compatibility."""

    url: HttpUrl | None = None
    raw_content: dict[str, Any] | None = None
    model: LLMModel = "gemini"


class TextGenResponse(_Strict):
    headline: str
    body: str
    cta: str
    image_prompt: str
    model_used: str


class ModelInfo(_Strict):
    """One row in the ``GET /api/ai/models`` response."""

    id: str
    provider: str
    kind: Literal["text", "image"]
    description: str | None = None


class ModelsResponse(_Strict):
    items: list[ModelInfo]


# ── Public surface ─────────────────────────────────────────────────────


__all__ = [
    "AdCreate",
    "AdListResponse",
    "AdResponse",
    "AdStatus",
    "AdUpdate",
    "AdVersionListResponse",
    "AdVersionResponse",
    "CleanRequest",
    "CleanResponse",
    "ImageModel",
    "ImageRequest",
    "ImageResponse",
    "ImageSize",
    "LLMModel",
    "ModelInfo",
    "ModelsResponse",
    "OutlineRequest",
    "OutlineResponse",
    "PromptRequest",
    "PromptResponse",
    "ScrapeRequest",
    "ScrapeResponse",
    "ScrapedContent",
    "TextGenRequest",
    "TextGenResponse",
    "UserMe",
]
