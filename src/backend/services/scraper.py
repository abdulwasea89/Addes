"""Cloudflare Browser Rendering scraping service.

We use Cloudflare's **synchronous** Browser Rendering REST endpoints — they
return content in a single round-trip, unlike ``/crawl`` which is async and
needs job polling. Two endpoints get composed here:

* ``POST /browser-rendering/markdown`` — full page text as clean markdown,
  with JS executed and unnecessary scripts/styles stripped.
* ``POST /browser-rendering/links`` — every link on the page, used to harvest
  image URLs (the markdown endpoint inlines some images but not all).

The HTML ``<title>`` and meta description are extracted from a third call to
``/content`` (raw HTML) using a tiny regex pass — we don't pull in BeautifulSoup
for two fields.

Failure semantics
-----------------
Cloudflare's API can hiccup on slow target sites. We wrap each call in
:func:`tenacity.retry` with exponential backoff (3 attempts, 1s → 4s). When
the upstream stays broken, the caller gets a single :class:`ScraperError`,
which routers will translate to HTTP 502.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from backend.config import Settings, get_settings

# ── Public errors ─────────────────────────────────────────────────────


class ScraperError(RuntimeError):
    """Raised when Cloudflare scraping fails permanently after retries."""


# ── Result shape ──────────────────────────────────────────────────────


@dataclass(slots=True, frozen=True)
class ScrapedPage:
    """Plain-data result returned by :func:`scrape`.

    Matches :class:`backend.schemas.ScrapedContent` so routers can hand it
    straight to Pydantic without remapping fields.
    """

    title: str | None
    description: str | None
    text: str
    images: list[str]
    metadata: dict[str, Any]


# ── Internals ─────────────────────────────────────────────────────────

_BASE = "https://api.cloudflare.com/client/v4"
_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)

# Loose matchers for <title> and meta description / og tags. We deliberately
# stay tolerant — a missing tag returns None, never raises.
_RE_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_RE_META = re.compile(
    r'<meta\s+[^>]*?(?:name|property)\s*=\s*"([^"]+)"[^>]*?content\s*=\s*"([^"]*)"',
    re.IGNORECASE,
)
_META_KEYS_OF_INTEREST = {
    "description",
    "og:title",
    "og:description",
    "og:image",
    "twitter:title",
    "twitter:description",
    "twitter:image",
}


def _require_credentials(settings: Settings) -> tuple[str, str]:
    """Return ``(account_id, api_token)`` or raise :class:`ScraperError`."""
    if not settings.cloudflare_account_id or not settings.cloudflare_api_key:
        raise ScraperError(
            "Cloudflare credentials missing — set CLOUDFLARE_ACCOUNT_ID and "
            "CLOUDFLARE_API_KEY before calling the scraper."
        )
    return settings.cloudflare_account_id, settings.cloudflare_api_key.get_secret_value()


def _is_retryable(exc: BaseException) -> bool:
    """Retry on transport errors and on 429/5xx — never on auth/4xx bugs.

    A 401 means the API key is wrong, a 400 means our payload is wrong, and
    a 404 means the account ID is wrong. None of those get better by retrying.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status == 429 or 500 <= status < 600
    return isinstance(exc, httpx.TransportError)


def _retrying() -> AsyncRetrying:
    """Same retry policy for every endpoint — keep it predictable."""
    return AsyncRetrying(
        stop=stop_after_attempt(4),
        # Browser Rendering's 429 window is short; back off slightly longer.
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception(_is_retryable),
        reraise=True,
    )


async def _post(
    client: httpx.AsyncClient,
    account_id: str,
    token: str,
    action: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    """POST to a Browser Rendering quick action.

    All upstream failures (HTTP 4xx/5xx, transport errors, API ``success=false``
    envelopes) are wrapped in :class:`ScraperError` so callers handle exactly
    one exception type. Retryable categories (429, 5xx, transport) are tried
    up to four times before being wrapped.
    """
    url = f"{_BASE}/accounts/{account_id}/browser-rendering/{action}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    try:
        async for attempt in _retrying():
            with attempt:
                resp = await client.post(url, headers=headers, json=body, timeout=_TIMEOUT)
                resp.raise_for_status()
                data: dict[str, Any] = resp.json()
                if not data.get("success", False):
                    # API-level failure — surface upstream errors verbatim. We
                    # raise ScraperError directly so the retry decider treats it
                    # as a permanent error, not a transport blip.
                    raise ScraperError(
                        f"Cloudflare {action} returned success=false: {data.get('errors')!r}"
                    )
                return data
    except ScraperError:
        raise
    except httpx.HTTPStatusError as exc:
        # Non-retryable 4xx (or 5xx after retries exhausted) — pull the
        # upstream error body if it's JSON so the caller sees the real cause.
        try:
            err_payload: Any = exc.response.json()
        except ValueError:
            err_payload = exc.response.text[:200]
        raise ScraperError(
            f"Cloudflare {action} HTTP {exc.response.status_code}: {err_payload!r}"
        ) from exc
    except httpx.HTTPError as exc:
        raise ScraperError(f"Cloudflare {action} transport error: {exc}") from exc
    # Unreachable: AsyncRetrying with reraise=True either returns above or raises.
    raise ScraperError(f"Cloudflare {action} exhausted retries")


def _extract_html_meta(html: str) -> tuple[str | None, str | None, dict[str, str]]:
    """Pull ``<title>``, meta ``description``, and notable meta tags from HTML."""
    title_match = _RE_TITLE.search(html)
    title = title_match.group(1).strip() if title_match else None

    meta: dict[str, str] = {}
    for key, value in _RE_META.findall(html):
        k = key.lower().strip()
        if k in _META_KEYS_OF_INTEREST:
            meta[k] = value.strip()

    description = meta.get("description") or meta.get("og:description")
    return title, description, meta


def _coerce_image_urls(links: Any) -> list[str]:
    """Browser-rendering ``/links`` returns a list of strings or objects.

    Be defensive — keep only items that look like absolute http(s) URLs to an
    image. We don't fetch the images, just collect candidates for downstream
    AI prompting.
    """
    out: list[str] = []
    if not isinstance(links, list):
        return out
    image_exts = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg")
    for item in links:
        url = item if isinstance(item, str) else item.get("url") if isinstance(item, dict) else None
        if not isinstance(url, str):
            continue
        if not url.startswith(("http://", "https://")):
            continue
        if url.lower().split("?", 1)[0].endswith(image_exts):
            out.append(url)
    # De-dup while preserving order.
    return list(dict.fromkeys(out))


# ── Public API ────────────────────────────────────────────────────────


async def scrape(url: str, settings: Settings | None = None) -> ScrapedPage:
    """Scrape ``url`` via Cloudflare Browser Rendering and return a clean record.

    Raises :class:`ScraperError` if Cloudflare credentials are missing or the
    upstream stays broken after retries.
    """
    s = settings or get_settings()
    account_id, token = _require_credentials(s)
    body = {"url": url}

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        # Browser Rendering enforces a small per-account concurrency cap
        # (free plan is 2). Run sequentially with a tiny pause so we never
        # trip the 429 limiter even when retried.
        markdown_resp = await _post(client, account_id, token, "markdown", body)
        await asyncio.sleep(0.3)
        content_resp = await _post(client, account_id, token, "content", body)
        await asyncio.sleep(0.3)
        links_resp = await _post(client, account_id, token, "links", body)

    markdown = markdown_resp.get("result", "")
    if not isinstance(markdown, str):
        markdown = ""

    html_result = content_resp.get("result", "")
    html = html_result if isinstance(html_result, str) else ""
    title, description, meta_tags = _extract_html_meta(html)

    images = _coerce_image_urls(links_resp.get("result"))

    return ScrapedPage(
        title=title,
        description=description,
        text=markdown,
        images=images,
        metadata={
            "og_title": meta_tags.get("og:title"),
            "og_description": meta_tags.get("og:description"),
            "og_image": meta_tags.get("og:image"),
            "twitter_title": meta_tags.get("twitter:title"),
            "twitter_description": meta_tags.get("twitter:description"),
            "twitter_image": meta_tags.get("twitter:image"),
            "source_url": url,
        },
    )


__all__ = ["ScrapedPage", "ScraperError", "scrape"]
