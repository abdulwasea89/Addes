"""Supabase JWT verification + ``get_current_user`` FastAPI dependency.

Supabase issues access tokens in two flavors depending on project age:

* **Asymmetric (ES256 / RS256)** — modern projects. The public key is exposed
  at ``{SUPABASE_URL}/auth/v1/.well-known/jwks.json``. We fetch the JWKS once,
  cache it in-process with a TTL, and pick the right key for each token by
  its ``kid`` header.
* **Symmetric (HS256)** — legacy projects. The signing key is the value of
  ``SUPABASE_JWT_SECRET``. Used as a fallback when a token has no ``kid`` or
  the JWKS does not contain a match.

Both code paths converge on the same :class:`CurrentUser` result so callers
do not need to care which scheme the project uses.

Usage in a router::

    @router.get("/me")
    async def me(user: CurrentUser = Depends(get_current_user)) -> ...:
        return {"user_id": user.id, "email": user.email}

The dependency raises a clean ``401`` when the header is missing, malformed,
expired, or fails signature verification.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Annotated, Any
from uuid import UUID

import httpx
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from backend.config import Settings, get_settings

# ``auto_error=False`` so we can return our own 401 with a uniform shape
# instead of FastAPI's default ``{"detail":"Not authenticated"}``.
_bearer = HTTPBearer(auto_error=False, scheme_name="SupabaseJWT")

# Algorithms Supabase is known to issue.
_ASYMMETRIC_ALGS = ("ES256", "RS256")
_SYMMETRIC_ALGS = ("HS256",)
_ALL_ALGS = _ASYMMETRIC_ALGS + _SYMMETRIC_ALGS

_JWKS_TTL_SECONDS = 3600  # 1 hour


@dataclass(frozen=True, slots=True)
class CurrentUser:
    """The authenticated principal extracted from a verified Supabase JWT."""

    id: UUID
    email: str | None
    role: str
    claims: dict[str, Any]


class _JWKSCache:
    """In-process JWKS cache with TTL.

    Process-local is correct here: each worker fetches once per hour. If a key
    rotation happens, expired tokens fail fast (401) and a fresh fetch picks
    up the new key on the next request.
    """

    def __init__(self, ttl: int = _JWKS_TTL_SECONDS) -> None:
        self._ttl = ttl
        self._lock = threading.Lock()
        self._keys_by_kid: dict[str, dict[str, Any]] = {}
        self._fetched_at: float = 0.0

    def _is_fresh(self) -> bool:
        return (
            bool(self._keys_by_kid)
            and (time.monotonic() - self._fetched_at) < self._ttl
        )

    def get(self, settings: Settings, kid: str) -> dict[str, Any] | None:
        """Return the JWK for ``kid``, refreshing the cache if needed."""
        with self._lock:
            if not self._is_fresh():
                self._refresh(settings)
            return self._keys_by_kid.get(kid)

    def _refresh(self, settings: Settings) -> None:
        url = f"{str(settings.supabase_url).rstrip('/')}/auth/v1/.well-known/jwks.json"
        try:
            with httpx.Client(timeout=5.0) as client:
                resp = client.get(url)
                resp.raise_for_status()
                payload = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            # Leave the previous cache in place if a refresh fails so that
            # transient network issues don't break all auth at once.
            if not self._keys_by_kid:
                raise _unauthorized(f"Cannot reach Supabase JWKS: {exc}") from exc
            return

        keys = payload.get("keys", [])
        self._keys_by_kid = {k["kid"]: k for k in keys if "kid" in k}
        self._fetched_at = time.monotonic()

    def clear(self) -> None:
        """Drop cached keys — useful in tests."""
        with self._lock:
            self._keys_by_kid.clear()
            self._fetched_at = 0.0


_jwks_cache = _JWKSCache()


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _decode_with_key(token: str, key: Any, algorithm: str) -> dict[str, Any]:
    """Run python-jose's decode with our standard option set."""
    return jwt.decode(
        token,
        key,
        algorithms=[algorithm],
        audience="authenticated",
        options={
            "require_exp": True,
            "require_sub": True,
            "verify_aud": True,
            "verify_signature": True,
        },
    )


def verify_jwt(token: str, settings: Settings) -> dict[str, Any]:
    """Decode and validate a Supabase access token.

    Raises :class:`HTTPException` (401) on any failure. Picks the verification
    algorithm based on the token header: asymmetric algs use JWKS, HS256 falls
    back to ``SUPABASE_JWT_SECRET``.
    """
    try:
        header = jwt.get_unverified_header(token)
    except JWTError as exc:
        raise _unauthorized(f"Malformed token: {exc}") from exc

    alg = header.get("alg")
    if alg not in _ALL_ALGS:
        raise _unauthorized(f"Unsupported JWT algorithm: {alg!r}")

    try:
        if alg in _ASYMMETRIC_ALGS:
            kid = header.get("kid")
            if not kid:
                raise _unauthorized("Token missing 'kid' for asymmetric algorithm")
            jwk = _jwks_cache.get(settings, kid)
            if jwk is None:
                # Force a refresh once in case the key was just rotated.
                _jwks_cache.clear()
                jwk = _jwks_cache.get(settings, kid)
            if jwk is None:
                raise _unauthorized(f"Unknown signing key id: {kid}")
            return _decode_with_key(token, jwk, alg)

        # HS256 — legacy path.
        secret = settings.supabase_jwt_secret.get_secret_value()
        return _decode_with_key(token, secret, alg)
    except HTTPException:
        raise
    except JWTError as exc:
        raise _unauthorized(f"Invalid or expired token: {exc}") from exc


async def get_current_user(
    settings: Annotated[Settings, Depends(get_settings)],
    creds: Annotated[
        HTTPAuthorizationCredentials | None, Depends(_bearer)
    ] = None,
) -> CurrentUser:
    """FastAPI dependency: returns the authenticated :class:`CurrentUser`.

    Raises 401 if the ``Authorization: Bearer <jwt>`` header is missing,
    malformed, or fails verification.
    """
    if creds is None or not creds.credentials:
        raise _unauthorized("Missing bearer token")

    claims = verify_jwt(creds.credentials, settings)

    sub = claims.get("sub")
    if not sub:
        raise _unauthorized("Token has no subject")
    try:
        user_id = UUID(str(sub))
    except ValueError as exc:
        raise _unauthorized("Token subject is not a UUID") from exc

    return CurrentUser(
        id=user_id,
        email=claims.get("email"),
        role=str(claims.get("role", "authenticated")),
        claims=claims,
    )


__all__ = ["CurrentUser", "get_current_user", "verify_jwt"]
