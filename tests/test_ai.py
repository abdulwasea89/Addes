"""Tests for the AI pipeline router.

Covers:
* LLM endpoints (clean / outline / prompt) for both ``gemini`` and ``groq``
* ``generate-image`` end-to-end: image_gen → storage → response
* ``generate-text`` SSE-streamed all-in-one pipeline
* ``GET /api/ai/models``
* Auth: every endpoint returns 401 without ``Authorization`` header
"""

from __future__ import annotations

import json

from starlette.testclient import TestClient

_RAW_CONTENT = {
    "title": "Test Page",
    "description": "A page about testing",
    "text": "This is test content for the AI pipeline.",
    "metadata": {"source_url": "https://example.com/"},
}

_CLEANED = {
    "brand_name": "TestBrand",
    "tagline": "We test",
    "key_benefits": ["Speed", "Quality"],
    "tone": "Professional",
    "colors": ["#000", "#fff"],
    "industry": "Tech",
    "model_used": "gemini-2.5-flash",
}

_OUTLINE = {
    "headline": "Test Headline",
    "body": "Test body copy",
    "cta": "Get Started",
    "keywords": ["test", "demo"],
    "model_used": "gemini-2.5-flash",
}

_GROQ_CLEANED = {
    "brand_name": "GroqBrand",
    "tagline": "Fast inference",
    "key_benefits": ["Speed"],
    "tone": "Technical",
    "colors": [],
    "industry": "AI",
    "model_used": "llama-3.3-70b-versatile",
}

_GROQ_OUTLINE = {
    "headline": "Groq Headline",
    "body": "Groq body",
    "cta": "Try Groq",
    "keywords": ["groq", "llama"],
    "model_used": "llama-3.3-70b-versatile",
}


# ── SSE parsing helper ────────────────────────────────────────────────


def _parse_sse(text: str) -> list[dict]:
    """Parse ``text/event-stream`` body into a list of ``{event, data}`` dicts."""
    events: list[dict] = []
    for block in text.strip().split("\n\n"):
        block = block.strip()
        if not block:
            continue
        lines = block.split("\n")
        event = ""
        raw_data = ""
        for line in lines:
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: "):
                raw_data = line[6:]
        if event and raw_data:
            events.append({"event": event, "data": json.loads(raw_data)})
    return events


# ── 401 — every endpoint rejects unauthenticated requests ─────────────


class TestAuth:
    def test_clean_requires_auth(self, client: TestClient) -> None:
        r = client.post("/api/ai/clean", json={"raw_content": {}, "model": "gemini"})
        assert r.status_code == 401

    def test_outline_requires_auth(self, client: TestClient) -> None:
        r = client.post("/api/ai/outline", json={"cleaned_data": {}, "model": "gemini"})
        assert r.status_code == 401

    def test_prompt_requires_auth(self, client: TestClient) -> None:
        r = client.post(
            "/api/ai/prompt",
            json={"outline": {}, "cleaned_data": {}, "model": "gemini"},
        )
        assert r.status_code == 401

    def test_generate_image_requires_auth(self, client: TestClient) -> None:
        r = client.post(
            "/api/ai/generate-image",
            json={"prompt": "test", "model": "flux"},
        )
        assert r.status_code == 401

    def test_generate_text_requires_auth(self, client: TestClient) -> None:
        r = client.post(
            "/api/ai/generate-text",
            json={"raw_content": _RAW_CONTENT, "model": "gemini"},
        )
        assert r.status_code == 401

    def test_models_requires_auth(self, client: TestClient) -> None:
        r = client.get("/api/ai/models")
        assert r.status_code == 401


# ── GET /api/ai/models ────────────────────────────────────────────────


class TestModels:
    def test_list_models(self, auth_client: TestClient) -> None:
        r = auth_client.get("/api/ai/models")
        assert r.status_code == 200
        data = r.json()
        ids = {m["id"] for m in data["items"]}
        # Must contain both LLM providers and the current image-model catalog
        # (Pollinations primary + Cloudflare fallbacks). Asserted as a subset
        # check so adding new providers doesn't break this test.
        expected_subset = {
            "gemini",
            "groq",
            "pollinations",
            "pollinations-turbo",
            "flux",
            "sdxl-lightning",
            "dreamshaper",
            "sd",
        }
        assert expected_subset.issubset(ids), (
            f"Missing models: {expected_subset - ids}"
        )


# ── LLM endpoints (gemini) ────────────────────────────────────────────


class TestGemini:
    def test_clean(self, auth_client: TestClient, mock_gemini: None) -> None:
        r = auth_client.post(
            "/api/ai/clean",
            json={"raw_content": _RAW_CONTENT, "model": "gemini"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["brand_name"] == "TestBrand"
        assert data["model_used"] == "gemini-2.5-flash"

    def test_outline(self, auth_client: TestClient, mock_gemini: None) -> None:
        r = auth_client.post(
            "/api/ai/outline",
            json={"cleaned_data": _CLEANED, "model": "gemini"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["headline"] == "Test Headline"
        assert data["model_used"] == "gemini-2.5-flash"

    def test_prompt(self, auth_client: TestClient, mock_gemini: None) -> None:
        r = auth_client.post(
            "/api/ai/prompt",
            json={"outline": _OUTLINE, "cleaned_data": _CLEANED, "model": "gemini"},
        )
        assert r.status_code == 200
        data = r.json()
        assert "prompt" in data
        assert data["model_used"] == "gemini-2.5-flash"


# ── LLM endpoints (groq) ──────────────────────────────────────────────


class TestGroq:
    def test_clean(self, auth_client: TestClient, mock_groq: None) -> None:
        r = auth_client.post(
            "/api/ai/clean",
            json={"raw_content": _RAW_CONTENT, "model": "groq"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["brand_name"] == "GroqBrand"
        assert data["model_used"] == "llama-3.3-70b-versatile"

    def test_outline(self, auth_client: TestClient, mock_groq: None) -> None:
        r = auth_client.post(
            "/api/ai/outline",
            json={"cleaned_data": _GROQ_CLEANED, "model": "groq"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["headline"] == "Groq Headline"

    def test_prompt(self, auth_client: TestClient, mock_groq: None) -> None:
        r = auth_client.post(
            "/api/ai/prompt",
            json={
                "outline": _GROQ_OUTLINE,
                "cleaned_data": _GROQ_CLEANED,
                "model": "groq",
            },
        )
        assert r.status_code == 200
        data = r.json()
        assert "prompt" in data


# ── Image generation ──────────────────────────────────────────────────


class TestImageGen:
    def test_generate_flux(
        self,
        auth_client: TestClient,
        mock_image_gen: None,
        mock_storage: None,
    ) -> None:
        r = auth_client.post(
            "/api/ai/generate-image",
            json={"prompt": "A test image", "model": "flux"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["image_url"].startswith("https://")
        assert data["model_used"] == "flux"
        assert data["prompt"] == "A test image"

    def test_generate_sd(
        self,
        auth_client: TestClient,
        mock_image_gen: None,
        mock_storage: None,
    ) -> None:
        r = auth_client.post(
            "/api/ai/generate-image",
            json={"prompt": "SD image", "model": "sd"},
        )
        assert r.status_code == 200
        assert r.json()["model_used"] == "sd"


# ── All-in-one generate-text SSE pipeline ─────────────────────────────


class TestGenerateText:
    def test_with_raw_content(
        self,
        auth_client: TestClient,
        mock_gemini: None,
    ) -> None:
        r = auth_client.post(
            "/api/ai/generate-text",
            json={"raw_content": _RAW_CONTENT, "model": "gemini"},
        )
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")

        events = _parse_sse(r.text)
        # raw_content path: no scrape step — starts with clean
        assert len(events) >= 4

        # Expected sequence: clean → outline → prompt → complete
        # Filter to status=complete so we only see each step once
        steps = [
            e["data"]["step"]
            for e in events
            if e["event"] == "step" and e["data"].get("status") == "complete"
        ]
        assert steps == ["clean", "outline", "prompt"]

        complete = [e for e in events if e["event"] == "complete"]
        assert len(complete) == 1
        payload = complete[0]["data"]
        assert payload["headline"] == "Test Headline"
        assert payload["body"] == "Test body copy"
        assert payload["cta"] == "Get Started"
        assert "image_prompt" in payload
        assert payload["model_used"] == "gemini-2.5-flash"

    def test_with_url(
        self,
        auth_client: TestClient,
        mock_scraper: None,
        mock_gemini: None,
    ) -> None:
        r = auth_client.post(
            "/api/ai/generate-text",
            json={"url": "https://example.com/", "model": "gemini"},
        )
        assert r.status_code == 200
        events = _parse_sse(r.text)

        # URL path: scrape → clean → outline → prompt → complete
        steps = [
            e["data"]["step"]
            for e in events
            if e["event"] == "step" and e["data"].get("status") == "complete"
        ]
        assert steps == ["scrape", "clean", "outline", "prompt"]

        complete = [e for e in events if e["event"] == "complete"]
        assert len(complete) == 1
        assert complete[0]["data"]["headline"] == "Test Headline"

    def test_neither_url_nor_content(self, auth_client: TestClient) -> None:
        r = auth_client.post(
            "/api/ai/generate-text",
            json={"model": "gemini"},
        )
        assert r.status_code == 400


# ── Schema validation ─────────────────────────────────────────────────


class TestValidation:
    def test_clean_empty_content(self, auth_client: TestClient, mock_gemini: None) -> None:
        r = auth_client.post(
            "/api/ai/clean",
            json={"raw_content": {}, "model": "gemini"},
        )
        assert r.status_code == 200

    def test_image_missing_prompt(self, auth_client: TestClient) -> None:
        r = auth_client.post(
            "/api/ai/generate-image",
            json={"model": "flux"},
        )
        assert r.status_code == 422

    def test_image_empty_prompt(self, auth_client: TestClient) -> None:
        r = auth_client.post(
            "/api/ai/generate-image",
            json={"prompt": "", "model": "flux"},
        )
        assert r.status_code == 422

    def test_unsupported_model(self, auth_client: TestClient) -> None:
        r = auth_client.post(
            "/api/ai/clean",
            json={"raw_content": _RAW_CONTENT, "model": "invalid-model"},
        )
        assert r.status_code == 422
