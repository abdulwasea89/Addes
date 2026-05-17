"""Tests for the ads CRUD router.

Covers:
* Happy-path: create, list, get-one, update, delete, versions
* Auth: every endpoint returns 401 without ``Authorization`` header
* 404 when the ad does not exist
* Pagination: ``limit`` and ``cursor`` query params, ``next_cursor`` in response
* Idempotency: ``Idempotency-Key`` header prevents duplicate creation
"""

from __future__ import annotations

from uuid import UUID

from starlette.testclient import TestClient

_AD_PAYLOAD = {
    "title": "Test Ad",
    "description": "A test ad for documentation",
    "source_url": "https://example.com/",
    "image_url": "https://example.com/image.jpg",
    "image_model": "flux",
    "status": "draft",
    "metadata": {"brand": "TestBrand", "cta": "Click here"},
}


def _ad_url(ad_id: str) -> str:
    return f"/api/ads/{ad_id}"


# ── 401 — every endpoint rejects unauthenticated requests ─────────────


class TestAuth:
    def test_create_requires_auth(self, client: TestClient) -> None:
        r = client.post("/api/ads", json=_AD_PAYLOAD)
        assert r.status_code == 401

    def test_list_requires_auth(self, client: TestClient) -> None:
        r = client.get("/api/ads")
        assert r.status_code == 401

    def test_get_one_requires_auth(self, client: TestClient) -> None:
        r = client.get("/api/ads/00000000-0000-0000-0000-000000000000")
        assert r.status_code == 401

    def test_update_requires_auth(self, client: TestClient) -> None:
        r = client.put(
            "/api/ads/00000000-0000-0000-0000-000000000000",
            json={"title": "x"},
        )
        assert r.status_code == 401

    def test_delete_requires_auth(self, client: TestClient) -> None:
        r = client.delete("/api/ads/00000000-0000-0000-0000-000000000000")
        assert r.status_code == 401

    def test_versions_requires_auth(self, client: TestClient) -> None:
        r = client.get("/api/ads/00000000-0000-0000-0000-000000000000/versions")
        assert r.status_code == 401


# ── Happy path ─────────────────────────────────────────────────────────


class TestHappyPath:
    def test_create_ad(self, auth_client: TestClient) -> None:
        r = auth_client.post("/api/ads", json=_AD_PAYLOAD)
        assert r.status_code == 201
        data = r.json()
        assert data["title"] == "Test Ad"
        assert data["status"] == "draft"
        assert data["metadata"]["brand"] == "TestBrand"
        assert "id" in data
        assert "created_at" in data
        assert UUID(data["id"])

    def test_list_ads(self, auth_client: TestClient) -> None:
        auth_client.post("/api/ads", json=_AD_PAYLOAD)
        auth_client.post("/api/ads", json={**_AD_PAYLOAD, "title": "Second Ad"})

        r = auth_client.get("/api/ads")
        assert r.status_code == 200
        data = r.json()
        assert data["count"] == 2
        assert data["next_cursor"] is None  # fewer results than limit
        titles = {item["title"] for item in data["items"]}
        assert titles == {"Test Ad", "Second Ad"}

    def test_get_ad(self, auth_client: TestClient) -> None:
        created = auth_client.post("/api/ads", json=_AD_PAYLOAD).json()

        r = auth_client.get(_ad_url(created["id"]))
        assert r.status_code == 200
        data = r.json()
        assert data["id"] == created["id"]
        assert data["title"] == "Test Ad"
        assert data["image_model"] == "flux"

    def test_update_ad(self, auth_client: TestClient) -> None:
        created = auth_client.post("/api/ads", json=_AD_PAYLOAD).json()

        r = auth_client.put(
            _ad_url(created["id"]),
            json={"title": "Updated Title", "status": "published"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["title"] == "Updated Title"
        assert data["status"] == "published"
        assert data["description"] == "A test ad for documentation"

    def test_delete_ad(self, auth_client: TestClient) -> None:
        created = auth_client.post("/api/ads", json=_AD_PAYLOAD).json()

        r = auth_client.delete(_ad_url(created["id"]))
        assert r.status_code == 204

        r = auth_client.get(_ad_url(created["id"]))
        assert r.status_code == 404

    def test_list_versions(self, auth_client: TestClient) -> None:
        created = auth_client.post("/api/ads", json=_AD_PAYLOAD).json()
        auth_client.put(_ad_url(created["id"]), json={"description": "Updated desc"})

        r = auth_client.get(f"/api/ads/{created['id']}/versions")
        assert r.status_code == 200
        data = r.json()
        assert data["count"] == 2
        assert data["items"][0]["title"] == "Test Ad"
        assert data["items"][1]["title"] == "Test Ad"


# ── Pagination ─────────────────────────────────────────────────────────


class TestPagination:
    def test_limit_clamps_results(self, auth_client: TestClient) -> None:
        for i in range(5):
            auth_client.post("/api/ads", json={**_AD_PAYLOAD, "title": f"Ad {i}"})

        r = auth_client.get("/api/ads?limit=2")
        assert r.status_code == 200
        data = r.json()
        assert len(data["items"]) == 2
        assert data["count"] == 2
        # next_cursor present when more items exist
        assert data["next_cursor"] is not None

    def test_limit_one(self, auth_client: TestClient) -> None:
        auth_client.post("/api/ads", json=_AD_PAYLOAD)

        r = auth_client.get("/api/ads?limit=1")
        assert r.status_code == 200
        data = r.json()
        assert len(data["items"]) == 1

    def test_invalid_limit_rejected(self, auth_client: TestClient) -> None:
        r = auth_client.get("/api/ads?limit=0")
        assert r.status_code == 422

        r = auth_client.get("/api/ads?limit=101")
        assert r.status_code == 422

    def test_cursor_fetches_next_page(self, auth_client: TestClient) -> None:
        for i in range(3):
            auth_client.post("/api/ads", json={**_AD_PAYLOAD, "title": f"Ad {i}"})

        r = auth_client.get("/api/ads?limit=2")
        assert r.status_code == 200
        first = r.json()
        assert len(first["items"]) == 2
        assert first["next_cursor"] is not None

        # Using cursor returns a valid response
        r2 = auth_client.get(f"/api/ads?limit=2&cursor={first['next_cursor']}")
        assert r2.status_code == 200

        # Fewer results than limit → no next_cursor
        r3 = auth_client.get("/api/ads?limit=10")
        assert r3.status_code == 200
        assert r3.json()["next_cursor"] is None


# ── Idempotency ────────────────────────────────────────────────────────


class TestIdempotency:
    def test_idempotent_create_returns_same(self, auth_client: TestClient) -> None:
        key = "test-key-1"
        headers = {"Idempotency-Key": key, "Content-Type": "application/json"}

        r1 = auth_client.post("/api/ads", json=_AD_PAYLOAD, headers=headers)
        assert r1.status_code == 201
        data1 = r1.json()

        r2 = auth_client.post("/api/ads", json=_AD_PAYLOAD, headers=headers)
        assert r2.status_code == 201
        data2 = r2.json()
        # Same response body
        assert data2 == data1
        # Replay header present
        assert r2.headers.get("Idempotency-Replay") == "true"

    def test_different_keys_create_separate_ads(self, auth_client: TestClient) -> None:
        r1 = auth_client.post(
            "/api/ads",
            json=_AD_PAYLOAD,
            headers={"Idempotency-Key": "key-a", "Content-Type": "application/json"},
        )
        r2 = auth_client.post(
            "/api/ads",
            json=_AD_PAYLOAD,
            headers={"Idempotency-Key": "key-b", "Content-Type": "application/json"},
        )
        assert r1.status_code == 201
        assert r2.status_code == 201
        assert r1.json()["id"] != r2.json()["id"]

    def test_no_key_creates_normally(self, auth_client: TestClient) -> None:
        r1 = auth_client.post("/api/ads", json=_AD_PAYLOAD)
        r2 = auth_client.post("/api/ads", json=_AD_PAYLOAD)
        assert r1.status_code == 201
        assert r2.status_code == 201
        assert r1.json()["id"] != r2.json()["id"]


# ── Edge cases ─────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_get_nonexistent_ad(self, auth_client: TestClient) -> None:
        r = auth_client.get("/api/ads/00000000-0000-0000-0000-000000000000")
        assert r.status_code == 404

    def test_update_nonexistent_ad(self, auth_client: TestClient) -> None:
        r = auth_client.put(
            "/api/ads/00000000-0000-0000-0000-000000000000",
            json={"title": "Nope"},
        )
        assert r.status_code == 404

    def test_delete_nonexistent_ad(self, auth_client: TestClient) -> None:
        r = auth_client.delete("/api/ads/00000000-0000-0000-0000-000000000000")
        assert r.status_code == 404

    def test_versions_nonexistent_ad(self, auth_client: TestClient) -> None:
        r = auth_client.get("/api/ads/00000000-0000-0000-0000-000000000000/versions")
        assert r.status_code == 404

    def test_create_ad_missing_title(self, auth_client: TestClient) -> None:
        r = auth_client.post("/api/ads", json={"description": "no title"})
        assert r.status_code == 422

    def test_create_ad_empty_title(self, auth_client: TestClient) -> None:
        r = auth_client.post("/api/ads", json={**_AD_PAYLOAD, "title": ""})
        assert r.status_code == 422

    def test_create_ad_invalid_status(self, auth_client: TestClient) -> None:
        r = auth_client.post("/api/ads", json={**_AD_PAYLOAD, "status": "unknown"})
        assert r.status_code == 422

    def test_put_can_null_description(self, auth_client: TestClient) -> None:
        created = auth_client.post("/api/ads", json=_AD_PAYLOAD).json()

        r = auth_client.put(
            _ad_url(created["id"]),
            json={"description": None},
        )
        assert r.status_code == 200
        assert r.json()["description"] is None
