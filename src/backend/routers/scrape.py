"""Scrape router — ``POST /api/scrape``.

Thin HTTP wrapper around :mod:`backend.services.scraper`. The router does
three jobs:

1. Validate the request body against :class:`ScrapeRequest` (URL must be a
   well-formed http(s) URL).
2. Require an authenticated caller — scraping is metered by Cloudflare per
   project, so anonymous traffic would burn quota.
3. Translate :class:`ScraperError` into HTTP 502 with the upstream message
   preserved (clients still see a useful error, not a generic 500).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from backend.auth import CurrentUser, get_current_user
from backend.schemas import ScrapedContent, ScrapeRequest, ScrapeResponse
from backend.services import scraper

router = APIRouter(prefix="/api/scrape", tags=["scrape"])


@router.post(
    "",
    response_model=ScrapeResponse,
    status_code=status.HTTP_200_OK,
    summary="Scrape a URL via Cloudflare Browser Rendering",
)
async def scrape_url(
    body: ScrapeRequest,
    _user: Annotated[CurrentUser, Depends(get_current_user)],
) -> ScrapeResponse:
    try:
        page = await scraper.scrape(str(body.url))
    except scraper.ScraperError as exc:
        # Bad gateway — upstream (Cloudflare) failed. Pass the message
        # through so callers can distinguish e.g. NSFW prompts from network
        # errors without us inventing a code taxonomy.
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc

    content = ScrapedContent(
        title=page.title,
        description=page.description,
        text=page.text,
        images=page.images,
        metadata=page.metadata,
    )
    return ScrapeResponse(success=True, content=content)
