"""Groq LLM service for the AI pipeline (Agno-backed).

Drop-in fallback for :mod:`backend.services.gemini`. Same three operations,
same return dict shape, same :class:`GroqError` contract — only the model
provider changes:

* :func:`clean` — turn scraped content into a brand record.
* :func:`outline` — turn a brand record into ad copy.
* :func:`craft_prompt` — turn an outline + brand into an image-generation
  prompt.

Why Agno
--------
Previously this module called ``groq.AsyncGroq`` directly with
``response_format={'type': 'json_object'}`` and a verbose
"respond with EXACTLY these keys" system prompt, then validated the response
client-side. Moving to an Agno :class:`agno.agent.Agent` with
``output_schema=`` collapses all that into one uniform call — Agno handles
JSON-mode wiring + Pydantic validation internally — and lets us share the
exact same instructions wording with the Gemini service.

Model choice
------------
Default model is ``llama-3.3-70b-versatile`` (Groq's strongest general-purpose
model with JSON mode support). Callers can override per-call.
"""

from __future__ import annotations

import json
from typing import Any

from agno.agent import Agent
from agno.models.groq import Groq
from pydantic import BaseModel, Field

from backend.config import Settings, get_settings

# ── Public errors ─────────────────────────────────────────────────────


class GroqError(RuntimeError):
    """Raised when the Groq call fails or returns unparseable output."""


# ── Internal output schemas ───────────────────────────────────────────
# Identical to the schemas in :mod:`backend.services.gemini` — keeping them
# duplicated (rather than shared) preserves provider independence: each
# service module declares its own contract with the router.


class _BrandSchema(BaseModel):
    brand_name: str | None = Field(default=None)
    tagline: str | None = Field(default=None)
    key_benefits: list[str] = Field(default_factory=list)
    tone: str | None = Field(default=None)
    colors: list[str] = Field(default_factory=list)
    industry: str | None = Field(default=None)


class _OutlineSchema(BaseModel):
    headline: str
    body: str
    cta: str
    keywords: list[str] = Field(default_factory=list)


class _PromptSchema(BaseModel):
    prompt: str


# ── Constants ─────────────────────────────────────────────────────────

DEFAULT_MODEL = "llama-3.3-70b-versatile"

# Instructions match the Gemini service verbatim — the Pydantic schema is now
# the source of truth for shape, so we no longer need the "Return JSON with
# EXACTLY these keys" boilerplate Groq used to need.

_CLEAN_INSTRUCTION = (
    "You receive raw scraped content from a marketing webpage. Extract a "
    "structured brand profile. Be concise: max 5 key_benefits, max 3 colors "
    "(hex codes only). If a field is unknown, set it to null or [], never "
    "fabricate values."
)

_OUTLINE_INSTRUCTION = (
    "You receive a structured brand profile. Write a single ad outline. "
    "Headline must be under 12 words, body under 60 words, CTA under 5 words. "
    "Keywords: 3-8 short tokens, lowercase."
)

_PROMPT_INSTRUCTION = (
    "You are a senior art director writing ONE image-generation prompt for "
    "FLUX / SDXL / Pollinations. The output must read as a finished AD "
    "POSTER MOCKUP — not a stock photo, not a background plate, not an "
    "abstract icon. Imagine the result being shown on Instagram or as a "
    "Facebook Ad: it must feel commercial, branded, and intentional.\n"
    "\n"
    "You receive an outline (headline, body, CTA, keywords) and a brand "
    "profile (brand_name, tagline, key_benefits, tone, colors, industry). "
    "Build a single-line prompt that follows this exact structure "
    "(in order, comma-separated, no section labels):\n"
    "\n"
    "1) FORMAT: 'modern social-media advertising poster, 1:1 square format, "
    "Instagram ad aesthetic' (or '4:5 vertical Instagram Story ad' if the "
    "brand tone is youthful).\n"
    "2) LAYOUT (this is what makes it look like an AD, not a background):\n"
    "   - Describe a clear hero subject in the upper-two-thirds (a real, "
    "concrete product or symbol from the brand's industry).\n"
    "   - Add a bold flat-color block, ribbon, or rounded rectangle in the "
    "lower third sized for a headline overlay — the kind real designers "
    "leave blank. Describe it as 'a solid colorblock occupying the bottom "
    "third, reserved for headline typography' (DO NOT render the text — a "
    "designer will composite it on top later).\n"
    "   - Optionally add a small accent shape (circle, badge, sticker) in "
    "one corner for the CTA — again, empty, designed to be overlaid.\n"
    "3) SUBJECT: one concrete, photographable focal point that visually "
    "embodies the brand's key benefit — never a logo, never on-image text. "
    "Be specific (e.g. 'a single chrome espresso cup with a curl of steam', "
    "not 'a coffee thing').\n"
    "4) STYLE: pick one that matches brand tone — 'commercial product "
    "photography', 'editorial advertising photography', 'flat vector "
    "advertising illustration', or 'modern minimalist poster graphic'.\n"
    "5) LIGHTING: name a real lighting setup (softbox, rim light, golden "
    "hour, studio strobes, overcast). Never 'beautiful lighting'.\n"
    "6) COLOR PALETTE: the dominant headline-overlay colorblock and any "
    "accent shapes MUST use brand hex codes from `colors` (e.g. 'colorblock "
    "in #0066FF cobalt blue, white headline space'). If `colors` is empty, "
    "pick a 2-color palette that fits the industry and state both hex codes.\n"
    "7) CAMERA OR RENDER: '85mm at f/2.8, shallow depth of field' for photo "
    "subjects; 'flat vector, no perspective, crisp geometry' for graphic "
    "posters.\n"
    "8) FINISH: 'hyper-detailed, sharp focus, commercial advertising "
    "quality, 8K' for photo; 'crisp vectors, clean geometry, Adobe "
    "Illustrator finish' for graphic.\n"
    "9) NEGATIVE CUES (always append verbatim): 'no text, no letters, no "
    "lorem ipsum, no fake words, no watermark, no logo, no UI chrome, no "
    "human hands, no extra fingers, no distorted anatomy, no cluttered "
    "background, no plain background'.\n"
    "\n"
    "Hard rules:\n"
    "- The result must LOOK like an ad. If you can imagine it as a desktop "
    "wallpaper or a stock-photo background, your prompt is wrong — add "
    "more poster layout cues.\n"
    "- NEVER ask the model to render text — Flux/SDXL/Pollinations "
    "hallucinate gibberish. Leave space for a designer to overlay text.\n"
    "- The hero subject must be a real photographable thing tied to the "
    "industry, not an abstract icon or 'document with placeholder lines'.\n"
    "- NEVER include placeholder tokens like '[brand]' — substitute the "
    "real brand_name only if it can appear as a subject (e.g. on a "
    "product), and even then prefer abstract symbols over wordmarks.\n"
    "- NEVER reference real people, celebrities, or copyrighted characters.\n"
    "- If the brand profile is thin (e.g. just 'Example Domain'), invent "
    "a concrete subject from the industry — DO NOT fall back to vague "
    "abstractions or 'a document icon'.\n"
    "- Output ONE single line. No preamble. No explanations. No quotes "
    "around the prompt."
)


# ── Internals ─────────────────────────────────────────────────────────


def _make_agent(
    settings: Settings,
    model: str,
    instruction: str,
    schema: type[BaseModel],
) -> Agent:
    """Build a fresh Agno :class:`Agent` bound to Groq with structured output."""
    if settings.groq_api_key is None:
        raise GroqError("GROQ_API_KEY is not set.")
    return Agent(
        model=Groq(
            id=model,
            api_key=settings.groq_api_key.get_secret_value(),
        ),
        instructions=instruction,
        output_schema=schema,
    )


async def _run_agent(
    settings: Settings,
    model: str,
    instruction: str,
    user_payload: str,
    schema: type[BaseModel],
) -> dict[str, Any]:
    """Run the agent and return a validated dict.

    Any provider error is collapsed to a single :class:`GroqError` so the
    router's existing 502 handler treats all failure modes uniformly.
    """
    agent = _make_agent(settings, model, instruction, schema)
    try:
        response = await agent.arun(input=user_payload)
    except Exception as exc:  # pragma: no cover — network errors at runtime
        raise GroqError(f"Groq call failed: {exc}") from exc

    content = response.content
    if not isinstance(content, schema):
        raise GroqError(
            f"Groq returned unexpected payload type for {schema.__name__}: "
            f"{type(content).__name__}"
        )
    return content.model_dump()


# ── Public API ────────────────────────────────────────────────────────


async def clean(
    raw_content: dict[str, Any],
    *,
    model: str = DEFAULT_MODEL,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Clean & structure scraped content into a brand profile."""
    s = settings or get_settings()
    payload = json.dumps(raw_content, ensure_ascii=False)
    result = await _run_agent(s, model, _CLEAN_INSTRUCTION, payload, _BrandSchema)
    result["model_used"] = model
    return result


async def outline(
    cleaned_data: dict[str, Any],
    *,
    model: str = DEFAULT_MODEL,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Turn a brand profile into a headline / body / CTA / keywords outline."""
    s = settings or get_settings()
    payload = json.dumps(cleaned_data, ensure_ascii=False)
    result = await _run_agent(s, model, _OUTLINE_INSTRUCTION, payload, _OutlineSchema)
    result["model_used"] = model
    return result


async def craft_prompt(
    outline_data: dict[str, Any],
    cleaned_data: dict[str, Any],
    *,
    model: str = DEFAULT_MODEL,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Compose a single minimalist image-generation prompt."""
    s = settings or get_settings()
    payload = json.dumps(
        {"outline": outline_data, "brand": cleaned_data}, ensure_ascii=False
    )
    result = await _run_agent(s, model, _PROMPT_INSTRUCTION, payload, _PromptSchema)
    result["model_used"] = model
    return result


__all__ = ["DEFAULT_MODEL", "GroqError", "clean", "craft_prompt", "outline"]
