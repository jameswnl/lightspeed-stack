"""OIDC client-credentials token provider for the OpenShell spawner.

Mints and caches access tokens via the OIDC client-credentials grant, for
gateways secured with short-lived OIDC tokens that a static, pre-minted
bearer_token config value can't keep up with. Passed to cloud-agents'
OpenShellSpawner as bearer_token_provider -- get_token() is called fresh
right before each gRPC channel is constructed, so this class owns the
caching: only re-mints when the cached token is near its expiry.
"""

# pylint: disable=too-few-public-methods

from __future__ import annotations

import threading
import time
from typing import Any, Optional

import httpx

from log import get_logger

logger = get_logger(__name__)

_EXPIRY_SAFETY_MARGIN_SECONDS = 30


class OidcTokenFetchError(RuntimeError):
    """Raised when fetching an OIDC access token fails."""


class OidcClientCredentialsTokenProvider:
    """Mints and caches OIDC access tokens via the client-credentials grant."""

    def __init__(
        self,
        issuer: str,
        client_id: str,
        client_secret: str,
        audience: Optional[str] = None,
    ) -> None:
        """Initialize the provider.

        Parameters:
            issuer: OIDC issuer base URL, e.g.
                'https://keycloak.example.com/realms/agents'. The token
                endpoint is derived as '{issuer}/protocol/openid-connect/token'.
            client_id: OIDC client ID.
            client_secret: OIDC client secret.
            audience: Optional OIDC audience to request.
        """
        self._token_endpoint = issuer.rstrip("/") + "/protocol/openid-connect/token"
        self._client_id = client_id
        self._client_secret = client_secret
        self._audience = audience
        self._cached_token: Optional[str] = None
        self._expires_at: float = 0.0
        self._lock = threading.Lock()

    def get_token(self) -> str:
        """Return a cached token, or mint a fresh one if near expiry.

        Guarded by a lock: get_token() is called from multiple threads
        (asyncio.to_thread workers, gRPC interceptor threads) under a
        shared spawner instance, so an unguarded check-then-fetch could
        race and mint redundant tokens right at the expiry boundary.

        Returns:
            A valid OIDC access token.

        Raises:
            OidcTokenFetchError: If minting a fresh token fails.
        """
        with self._lock:
            if (
                self._cached_token is not None
                and time.time() < self._expires_at - _EXPIRY_SAFETY_MARGIN_SECONDS
            ):
                return self._cached_token
            return self._fetch_token()

    def _fetch_token(self) -> str:
        """Mint a fresh access token via the client-credentials grant.

        Returns:
            The newly-minted access token.

        Raises:
            OidcTokenFetchError: If the request fails, returns a non-2xx
                status, a non-JSON or malformed-shape body, or a
                non-numeric expires_in.
        """
        data: dict[str, str] = {
            "grant_type": "client_credentials",
            "client_id": self._client_id,
            "client_secret": self._client_secret,
        }
        if self._audience:
            data["audience"] = self._audience

        try:
            response = httpx.post(self._token_endpoint, data=data, timeout=10.0)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise OidcTokenFetchError(
                f"Failed to fetch OIDC access token from {self._token_endpoint}: {exc}"
            ) from exc

        try:
            payload: dict[str, Any] = response.json()
        except ValueError as exc:
            raise OidcTokenFetchError(
                f"OIDC token endpoint {self._token_endpoint} returned a "
                "non-JSON response"
            ) from exc

        token = payload.get("access_token")
        if not token:
            raise OidcTokenFetchError(
                f"OIDC token endpoint {self._token_endpoint} response had no "
                "access_token field"
            )

        try:
            expires_in = float(payload.get("expires_in", 0))
        except (TypeError, ValueError) as exc:
            raise OidcTokenFetchError(
                f"OIDC token endpoint {self._token_endpoint} returned a "
                f"non-numeric expires_in: {payload.get('expires_in')!r}"
            ) from exc

        self._cached_token = token
        self._expires_at = time.time() + expires_in
        logger.info("Fetched fresh OIDC access token (expires_in=%ss)", expires_in)
        return str(token)
