"""AI pipeline router.

Six endpoints under ``/api/ai`` that expose each stage of the
``scrape → clean → outline → prompt → image → save`` flow. The router never
talks to providers directly — it dispatches to a service module based on the
``model`` field in the request body (``gemini`` or ``groq``) and wraps service
errors in ``HTTPException``.

Routing convention
------------------
``CleanRequest.model`` / ``OutlineRequest.model`` / ``PromptRequest.model`` use
the :data:`backend.schemas.LLMModel` literal — a single source of truth shared
between the wire schema and the dispatch table here.

The ``generate-image`` endpoint is the one place where two services compose:
:mod:`image_gen` produces bytes, :mod:`storage` persists them, and the
response carries only the stable Supabase URL — provider URLs (which expire)
are intentionally not exposed to clients.

The ``generate-text`` endpoint streams progress as Server-Sent Events::

    event: step
    data: {"step":"scrape","status":"in_progress"}

    event: step
    data: {"step":"scrape","status":"complete","result":{...}}

    event: step
    data: {"step":"clean","status":"in_progress"}

    …

    event: complete
    data: {"headline":"…","body":"…","cta":"…","image_prompt":"…","model_used":"…"}

    event: error
    data: {"detail":"…","request_id":"…"}

Clients should connect via ``EventSource`` (or a polyfill) and switch on
``event.type`` to drive a progress UI.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from backend.auth import CurrentUser, get_current_user
from backend.schemas import (
    CleanRequest,
    CleanResponse,
    ImageRequest,
    ImageResponse,
    LLMModel,
    ModelInfo,
    ModelsResponse,
    OutlineRequest,
    OutlineResponse,
    PromptRequest,
    PromptResponse,
    TextGenRequest,
)
from backend.services import gemini, groq, image_gen, scraper, storage

router = APIRouter(prefix="/api/ai", tags=["ai"])


# ── LLM dispatch ──────────────────────────────────────────────────────


_LLM_SERVICES = {
    "gemini": gemini,
    "groq": groq,
}


def _llm_for(model: LLMModel) -> Any:
    try:
        return _LLM_SERVICES[model]
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported model {model!r}",
        ) from exc


def _wrap_llm_error(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=str(exc),
    )


# ── SSE helpers ───────────────────────────────────────────────────────


def _sse_event(event: str, data: dict[str, Any]) -> str:
    """Format a single SSE ``event`` + ``data`` line pair."""
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


# ── Clean ─────────────────────────────────────────────────────────────


@router.post("/clean", response_model=CleanResponse)
async def ai_clean(
    body: CleanRequest,
    _user: Annotated[CurrentUser, Depends(get_current_user)],
) -> CleanResponse:
    service = _llm_for(body.model)
    try:
        result = await service.clean(body.raw_content)
    except (gemini.GeminiError, groq.GroqError) as exc:
        raise _wrap_llm_error(exc) from exc
    return CleanResponse(**result)


# ── Outline ───────────────────────────────────────────────────────────


@router.post("/outline", response_model=OutlineResponse)
async def ai_outline(
    body: OutlineRequest,
    _user: Annotated[CurrentUser, Depends(get_current_user)],
) -> OutlineResponse:
    service = _llm_for(body.model)
    try:
        result = await service.outline(body.cleaned_data)
    except (gemini.GeminiError, groq.GroqError) as exc:
        raise _wrap_llm_error(exc) from exc
    return OutlineResponse(**result)


# ── Prompt ────────────────────────────────────────────────────────────


@router.post("/prompt", response_model=PromptResponse)
async def ai_prompt(
    body: PromptRequest,
    _user: Annotated[CurrentUser, Depends(get_current_user)],
) -> PromptResponse:
    service = _llm_for(body.model)
    try:
        result = await service.craft_prompt(body.outline, body.cleaned_data)
    except (gemini.GeminiError, groq.GroqError) as exc:
        raise _wrap_llm_error(exc) from exc
    return PromptResponse(**result)


# ── Generate image ────────────────────────────────────────────────────


@router.post("/generate-image", response_model=ImageResponse)
async def ai_generate_image(
    body: ImageRequest,
    user: Annotated[CurrentUser, Depends(get_current_user)],
) -> ImageResponse:
    try:
        image_bytes = await image_gen.generate(
            body.prompt,
            model=body.model,
            size=body.size,
        )
    except image_gen.ImageGenError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc

    try:
        upload = await storage.upload_image(str(user.id), image_bytes)
    except storage.StorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Storage upload failed: {exc}",
        ) from exc

    return ImageResponse(
        image_url=upload.url,
        storage_path=upload.path,
        model_used=body.model,
        prompt=body.prompt,
    )


# ── Generate text (SSE-streamed) ──────────────────────────────────────


@router.post(
    "/generate-text",
    response_class=StreamingResponse,
    responses={
        200: {
            "description": "Server-Sent Events stream. "
            "Consume with ``EventSource`` and switch on ``event.type``.",
            "content": {"text/event-stream": {}},
        },
    },
)
async def ai_generate_text(
    body: TextGenRequest,
    request: Request,
    _user: Annotated[CurrentUser, Depends(get_current_user)],
) -> StreamingResponse:
    """Run ``scrape → clean → outline → craft_prompt`` and stream each step.

    Accepts either a ``url`` (we scrape it) or pre-scraped ``raw_content``.
    Progress is delivered as Server-Sent Events so the frontend can drive a
    progress UI.
    """
    if body.url is None and body.raw_content is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide either 'url' or 'raw_content'.",
        )

    async def _generate() -> AsyncGenerator[str, None]:
        rid = getattr(request.state, "request_id", "-")
        raw: dict[str, Any] | None = None

        # ── Scrape (if url given) ────────────────────────────────────
        if body.url is not None:
            yield _sse_event("step", {"step": "scrape", "status": "in_progress"})
            try:
                page = await scraper.scrape(str(body.url))
            except scraper.ScraperError as exc:
                yield _sse_event("error", {"detail": str(exc), "request_id": rid})
                return
            raw = {
                "title": page.title,
                "description": page.description,
                "text": page.text,
                "metadata": page.metadata,
            }
            yield _sse_event("step", {"step": "scrape", "status": "complete", "result": raw})
        else:
            raw = body.raw_content

        if raw is None:
            yield _sse_event(
                "error",
                {"detail": "No content to process.", "request_id": rid},
            )
            return

        service = _llm_for(body.model)

        # ── Clean ────────────────────────────────────────────────────
        yield _sse_event("step", {"step": "clean", "status": "in_progress"})
        try:
            cleaned: dict[str, Any] = await service.clean(raw)
        except (gemini.GeminiError, groq.GroqError) as exc:
            yield _sse_event("error", {"detail": str(exc), "request_id": rid})
            return
        yield _sse_event("step", {"step": "clean", "status": "complete", "result": cleaned})

        # ── Outline ──────────────────────────────────────────────────
        yield _sse_event("step", {"step": "outline", "status": "in_progress"})
        try:
            outlined: dict[str, Any] = await service.outline(cleaned)
        except (gemini.GeminiError, groq.GroqError) as exc:
            yield _sse_event("error", {"detail": str(exc), "request_id": rid})
            return
        yield _sse_event("step", {"step": "outline", "status": "complete", "result": outlined})

        # ── Prompt ───────────────────────────────────────────────────
        yield _sse_event("step", {"step": "prompt", "status": "in_progress"})
        try:
            prompted: dict[str, Any] = await service.craft_prompt(outlined, cleaned)
        except (gemini.GeminiError, groq.GroqError) as exc:
            yield _sse_event("error", {"detail": str(exc), "request_id": rid})
            return
        yield _sse_event("step", {"step": "prompt", "status": "complete", "result": prompted})

        # ── Complete ─────────────────────────────────────────────────
        yield _sse_event(
            "complete",
            {
                "headline": outlined["headline"],
                "body": outlined["body"],
                "cta": outlined["cta"],
                "image_prompt": prompted["prompt"],
                "model_used": outlined["model_used"],
            },
        )

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── Models index ──────────────────────────────────────────────────────


@router.get("/models", response_model=ModelsResponse)
async def ai_models(
    _user: Annotated[CurrentUser, Depends(get_current_user)],
) -> ModelsResponse:
    items = [
        ModelInfo(
            id="gemini",
            provider="google",
            kind="text",
            description="Google Gemini 2.5 Flash — primary LLM for clean/outline/prompt.",
        ),
        ModelInfo(
            id="groq",
            provider="groq",
            kind="text",
            description="Llama 3.3 70B on Groq — fallback LLM.",
        ),
        ModelInfo(
            id="pollinations",
            provider="pollinations",
            kind="image",
            description=(
                "Pollinations.ai (FLUX-tier) — default image model. Free, "
                "no signup, anonymous-friendly. ~3-5 s at 1024 px."
            ),
        ),
        ModelInfo(
            id="pollinations-turbo",
            provider="pollinations",
            kind="image",
            description=(
                "Pollinations.ai (turbo) — lower quality, sub-second "
                "latency. Useful for thumbnails."
            ),
        ),
        ModelInfo(
            id="flux",
            provider="cloudflare",
            kind="image",
            description=(
                "FLUX.1 schnell on Cloudflare Workers AI. Marketing-grade "
                "1024 px output in ~3-5 s."
            ),
        ),
        ModelInfo(
            id="sdxl-lightning",
            provider="cloudflare",
            kind="image",
            description=(
                "ByteDance SDXL Lightning — fastest text-to-image, 2-step "
                "inference at 1024 px."
            ),
        ),
        ModelInfo(
            id="dreamshaper",
            provider="cloudflare",
            kind="image",
            description=(
                "Lykon DreamShaper 8 LCM — best free choice for "
                "photorealistic product shots."
            ),
        ),
        ModelInfo(
            id="sd",
            provider="cloudflare",
            kind="image",
            description="Stability AI SDXL base 1.0 — legacy, slower but versatile.",
        ),
    ]
    return ModelsResponse(items=items)
