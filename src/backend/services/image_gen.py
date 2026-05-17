"""Image generation service — Pollinations.ai (primary) + Cloudflare Workers AI.

Returns **raw image bytes** so the caller can persist them to Supabase Storage
via :mod:`backend.services.storage`. Provider URLs are deliberately never
returned: the storage service is the source of truth for stable image URLs.

Supported models (see :data:`backend.schemas.ImageModel`):

Pollinations.ai — truly free, no signup, no credit card:

* ``pollinations``     → FLUX-tier model on Pollinations (**default**). 1024 px
  in ~3-5 s. Anonymous tier: 1 req / 15 s. Add a ``POLLINATIONS_TOKEN`` in
  ``.env`` for a higher tier.
* ``pollinations-turbo`` → Pollinations' fast variant. Lower quality,
  sub-second latency. Useful for thumbnails.

Cloudflare Workers AI — 10 000 neurons/day free tier, requires
``CLOUDFLARE_API_KEY`` + ``CLOUDFLARE_ACCOUNT_ID``:

* ``flux``           → ``@cf/black-forest-labs/flux-1-schnell``.
* ``sdxl-lightning`` → ``@cf/bytedance/stable-diffusion-xl-lightning`` — 2-step.
* ``dreamshaper``    → ``@cf/lykon/dreamshaper-8-lcm`` — photorealistic.
* ``sd``             → ``@cf/stabilityai/stable-diffusion-xl-base-1.0`` — legacy.

* ``dalle3``         → raises :class:`ImageGenError` (OpenAI key not configured).
"""

from __future__ import annotations

import base64
import binascii
from typing import Any
from urllib.parse import quote

import httpx

from backend.config import Settings, get_settings

# ── Public errors ─────────────────────────────────────────────────────


class ImageGenError(RuntimeError):
    """Raised when image generation fails or a model is unsupported."""


# ── Provider routing ──────────────────────────────────────────────────


#: Pollinations model identifiers. The values map to the ``?model=`` query
#: parameter on ``https://image.pollinations.ai/prompt/...``.
_POLLINATIONS_MODELS: dict[str, str] = {
    "pollinations": "flux",
    "pollinations-turbo": "turbo",
}

#: Cloudflare Workers AI model paths.
_CLOUDFLARE_MODELS: dict[str, str] = {
    "flux": "@cf/black-forest-labs/flux-1-schnell",
    "sdxl-lightning": "@cf/bytedance/stable-diffusion-xl-lightning",
    "dreamshaper": "@cf/lykon/dreamshaper-8-lcm",
    "sd": "@cf/stabilityai/stable-diffusion-xl-base-1.0",
}

#: Cloudflare models where width/height are fixed by the model itself.
_FIXED_SIZE_MODELS = {"flux"}

#: SDXL-derived models on Cloudflare accept ``{prompt, width, height, num_steps}``.
_SDXL_FAMILY = {"sd", "sdxl-lightning", "dreamshaper"}

#: Per-model recommended step count (lower = faster, higher = sharper).
_DEFAULT_STEPS: dict[str, int] = {
    "flux": 4,
    "sd": 20,
    "sdxl-lightning": 2,
    "dreamshaper": 8,
}

# ── Endpoints ─────────────────────────────────────────────────────────

_CLOUDFLARE_BASE = "https://api.cloudflare.com/client/v4"
_POLLINATIONS_BASE = "https://image.pollinations.ai/prompt"

#: Generation can take up to 30s on SDXL; cold starts on Pollinations can
#: stretch to 60-90s. Be generous on read timeout.
_TIMEOUT = httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=10.0)


# ── Helpers ───────────────────────────────────────────────────────────


def _parse_size(size: str) -> tuple[int, int]:
    """``"1024x1024"`` → ``(1024, 1024)``. Raises on malformed input."""
    try:
        w_str, h_str = size.lower().split("x", 1)
        return int(w_str), int(h_str)
    except ValueError as exc:
        raise ImageGenError(f"Invalid size {size!r}; expected WIDTHxHEIGHT") from exc


# ── Cloudflare Workers AI ─────────────────────────────────────────────


def _cloudflare_inputs(model: str, size: str) -> dict[str, Any]:
    """Translate ``"1024x1024"`` into Cloudflare-Workers-AI input fields."""
    width, height = _parse_size(size)
    steps = _DEFAULT_STEPS.get(model, 8)
    if model in _FIXED_SIZE_MODELS:
        return {"steps": steps}
    if model in _SDXL_FAMILY:
        return {
            "width": max(256, min(2048, (width // 8) * 8 or 1024)),
            "height": max(256, min(2048, (height // 8) * 8 or 1024)),
            "num_steps": steps,
        }
    return {}


def _require_cloudflare(settings: Settings) -> tuple[str, str]:
    """Return ``(account_id, api_token)`` or raise."""
    if not settings.cloudflare_account_id or not settings.cloudflare_api_key:
        raise ImageGenError(
            "Cloudflare credentials missing — set CLOUDFLARE_ACCOUNT_ID and "
            "CLOUDFLARE_API_KEY to use Workers AI image generation."
        )
    return settings.cloudflare_account_id, settings.cloudflare_api_key.get_secret_value()


def _parse_cloudflare_response(resp: httpx.Response, cf_model_id: str) -> bytes:
    """Cloudflare returns image bytes in two shapes — handle both.

    * Most models: ``application/json`` with ``{"result": {"image": "<b64>"}}``.
    * SDXL base 1.0: raw ``image/*`` body.

    A JSON envelope with ``success=false`` is always an upstream error.
    """
    content_type = resp.headers.get("content-type", "").lower()

    if content_type.startswith("application/json"):
        payload = resp.json()
        if not payload.get("success", True):
            raise ImageGenError(
                f"Cloudflare AI {cf_model_id} returned success=false: "
                f"{payload.get('errors')!r}"
            )
        result = payload.get("result") or {}
        b64 = result.get("image") if isinstance(result, dict) else None
        if not isinstance(b64, str) or not b64:
            raise ImageGenError(
                f"Cloudflare AI {cf_model_id} JSON response missing 'image': "
                f"{str(payload)[:200]!r}"
            )
        try:
            return base64.b64decode(b64, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ImageGenError(
                f"Cloudflare AI {cf_model_id} returned non-base64 image data."
            ) from exc

    data = resp.content
    if not data:
        raise ImageGenError(
            f"Cloudflare AI {cf_model_id} returned empty image body."
        )
    return data


async def _generate_cloudflare(
    prompt: str,
    model: str,
    size: str,
    settings: Settings,
) -> bytes:
    """Generate one image via Cloudflare Workers AI and return raw bytes."""
    account_id, token = _require_cloudflare(settings)
    cf_model_id = _CLOUDFLARE_MODELS[model]
    body: dict[str, Any] = {"prompt": prompt, **_cloudflare_inputs(model, size)}
    url = f"{_CLOUDFLARE_BASE}/accounts/{account_id}/ai/run/{cf_model_id}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(url, headers=headers, json=body)
    except httpx.TransportError as exc:
        raise ImageGenError(f"Cloudflare AI transport error: {exc}") from exc

    if resp.status_code >= 400:
        try:
            err = resp.json()
        except ValueError:
            err = resp.text[:200]
        raise ImageGenError(
            f"Cloudflare AI {cf_model_id} HTTP {resp.status_code}: {err!r}"
        )
    return _parse_cloudflare_response(resp, cf_model_id)


# ── Pollinations.ai ───────────────────────────────────────────────────


async def _generate_pollinations(
    prompt: str,
    model: str,
    size: str,
    settings: Settings,
) -> bytes:
    """Generate one image via Pollinations.ai and return raw bytes.

    Pollinations is a free, anonymous-friendly image service. The API is a
    plain ``GET`` against ``image.pollinations.ai/prompt/{url-encoded-prompt}``
    with optional query parameters. The response body is the JPEG (or PNG)
    directly — no JSON envelope.
    """
    width, height = _parse_size(size)
    poll_model = _POLLINATIONS_MODELS[model]
    encoded_prompt = quote(prompt, safe="")
    url = f"{_POLLINATIONS_BASE}/{encoded_prompt}"
    params: dict[str, str | int] = {
        "model": poll_model,
        "width": max(64, min(2048, width)),
        "height": max(64, min(2048, height)),
        # ``safe=true`` returns an error on flagged prompts instead of a
        # blurred / placeholder image, which surfaces upstream issues cleanly
        # through our :class:`ImageGenError` plumbing.
        "safe": "true",
        # Keep generations off the public Pollinations feed by default — these
        # are user-owned marketing assets, not gallery material.
        "private": "true",
        # ``nologo=true`` removes Pollinations' watermark. Requires either a
        # ``POLLINATIONS_TOKEN`` Bearer or a registered referrer.
        "nologo": "true",
        # Identifies this app to Pollinations rate-limit telemetry so a future
        # token upgrade lands on the right account.
        "referrer": "adess-backend",
    }

    headers: dict[str, str] = {}
    token = getattr(settings, "pollinations_token", None)
    if token is not None and hasattr(token, "get_secret_value"):
        headers["Authorization"] = f"Bearer {token.get_secret_value()}"

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(url, params=params, headers=headers)
    except httpx.TransportError as exc:
        raise ImageGenError(f"Pollinations transport error: {exc}") from exc

    if resp.status_code >= 400:
        # Pollinations error bodies are short HTML or plain text — keep first
        # 200 chars for diagnostics.
        raise ImageGenError(
            f"Pollinations {poll_model} HTTP {resp.status_code}: "
            f"{resp.text[:200]!r}"
        )

    content_type = resp.headers.get("content-type", "").lower()
    data = resp.content
    if not data:
        raise ImageGenError(f"Pollinations {poll_model} returned empty body.")
    # Pollinations occasionally returns ``text/html`` on rate-limit / queue —
    # treat any non-image content-type as an error so we never upload garbage
    # to Supabase Storage.
    if not content_type.startswith("image/"):
        raise ImageGenError(
            f"Pollinations {poll_model} returned non-image content-type "
            f"{content_type!r}: {data[:200]!r}"
        )
    return data


# ── Public API ────────────────────────────────────────────────────────


async def generate(
    prompt: str,
    *,
    model: str = "pollinations",
    size: str = "1024x1024",
    settings: Settings | None = None,
) -> bytes:
    """Generate one image and return its raw bytes.

    :raises ImageGenError: on bad model name, missing creds, or upstream failure.
    """
    if not prompt or not prompt.strip():
        raise ImageGenError("prompt must be a non-empty string.")

    if model == "dalle3":
        raise ImageGenError(
            "dalle3 is not available — OPENAI_API_KEY is not configured. "
            "Use one of: pollinations, pollinations-turbo, flux, "
            "sdxl-lightning, dreamshaper, sd."
        )

    s = settings or get_settings()

    if model in _POLLINATIONS_MODELS:
        return await _generate_pollinations(prompt, model, size, s)
    if model in _CLOUDFLARE_MODELS:
        return await _generate_cloudflare(prompt, model, size, s)

    supported = sorted(set(_POLLINATIONS_MODELS) | set(_CLOUDFLARE_MODELS))
    raise ImageGenError(f"Unsupported model {model!r}. Supported: {supported}")


__all__ = ["ImageGenError", "generate"]
