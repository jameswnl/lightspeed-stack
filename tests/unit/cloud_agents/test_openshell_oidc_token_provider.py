"""Unit tests for the OpenShell OIDC client-credentials token provider."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from pytest_mock import MockerFixture

from workflow.openshell_oidc_token_provider import (
    OidcClientCredentialsTokenProvider,
    OidcTokenFetchError,
)


def _mock_post(
    mocker: MockerFixture, access_token: str = "token-1", expires_in: int = 300
) -> Any:
    """Patch httpx.post to return a canned client-credentials token response."""
    response = mocker.MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "access_token": access_token,
        "expires_in": expires_in,
    }
    return mocker.patch(
        "workflow.openshell_oidc_token_provider.httpx.post", return_value=response
    )


def test_get_token_fetches_on_first_call(mocker: MockerFixture) -> None:
    """First call to get_token() fetches a fresh token."""
    mock_post = _mock_post(mocker, access_token="fresh-token")
    provider = OidcClientCredentialsTokenProvider(
        issuer="https://keycloak.example.com/realms/agents",
        client_id="my-client",
        client_secret="my-secret",
    )

    token = provider.get_token()

    assert token == "fresh-token"
    mock_post.assert_called_once()


def test_get_token_sends_client_credentials_grant(mocker: MockerFixture) -> None:
    """The token request uses grant_type=client_credentials with the right params."""
    mock_post = _mock_post(mocker)
    provider = OidcClientCredentialsTokenProvider(
        issuer="https://keycloak.example.com/realms/agents",
        client_id="my-client",
        client_secret="my-secret",
        audience="my-audience",
    )

    provider.get_token()

    _, kwargs = mock_post.call_args
    assert (
        mock_post.call_args[0][0]
        == "https://keycloak.example.com/realms/agents/protocol/openid-connect/token"
    )
    assert kwargs["data"] == {
        "grant_type": "client_credentials",
        "client_id": "my-client",
        "client_secret": "my-secret",
        "audience": "my-audience",
    }


def test_get_token_omits_audience_when_not_configured(mocker: MockerFixture) -> None:
    """audience is omitted from the request body when not configured."""
    mock_post = _mock_post(mocker)
    provider = OidcClientCredentialsTokenProvider(
        issuer="https://keycloak.example.com/realms/agents",
        client_id="my-client",
        client_secret="my-secret",
    )

    provider.get_token()

    assert "audience" not in mock_post.call_args.kwargs["data"]


def test_get_token_strips_trailing_slash_from_issuer(mocker: MockerFixture) -> None:
    """A trailing slash on the issuer URL doesn't produce a double-slash endpoint."""
    mock_post = _mock_post(mocker)
    provider = OidcClientCredentialsTokenProvider(
        issuer="https://keycloak.example.com/realms/agents/",
        client_id="my-client",
        client_secret="my-secret",
    )

    provider.get_token()

    assert (
        mock_post.call_args[0][0]
        == "https://keycloak.example.com/realms/agents/protocol/openid-connect/token"
    )


def test_get_token_returns_cached_token_within_ttl(mocker: MockerFixture) -> None:
    """A second call within the token's lifetime returns the cached token."""
    mock_post = _mock_post(mocker, access_token="cached-token", expires_in=300)
    provider = OidcClientCredentialsTokenProvider(
        issuer="https://keycloak.example.com/realms/agents",
        client_id="my-client",
        client_secret="my-secret",
    )

    first = provider.get_token()
    second = provider.get_token()

    assert first == second == "cached-token"
    mock_post.assert_called_once()


def test_get_token_refetches_when_near_expiry(mocker: MockerFixture) -> None:
    """A call within the safety margin of expiry triggers a re-fetch."""
    fake_time = mocker.patch("workflow.openshell_oidc_token_provider.time.time")
    fake_time.return_value = 1000.0
    mock_post = _mock_post(mocker, access_token="token-1", expires_in=60)
    provider = OidcClientCredentialsTokenProvider(
        issuer="https://keycloak.example.com/realms/agents",
        client_id="my-client",
        client_secret="my-secret",
    )

    first = provider.get_token()

    # 35s elapsed, 25s remain -- inside the 30s safety margin.
    fake_time.return_value = 1035.0
    mock_post.return_value.json.return_value = {
        "access_token": "token-2",
        "expires_in": 60,
    }
    second = provider.get_token()

    assert first == "token-1"
    assert second == "token-2"
    assert mock_post.call_count == 2


def test_get_token_raises_clear_error_on_http_failure(mocker: MockerFixture) -> None:
    """An HTTP-level failure raises OidcTokenFetchError, not a raw httpx exception."""
    mocker.patch(
        "workflow.openshell_oidc_token_provider.httpx.post",
        side_effect=httpx.ConnectError("connection refused"),
    )
    provider = OidcClientCredentialsTokenProvider(
        issuer="https://keycloak.example.com/realms/agents",
        client_id="my-client",
        client_secret="my-secret",
    )

    with pytest.raises(OidcTokenFetchError, match="Failed to fetch OIDC access token"):
        provider.get_token()


def test_get_token_raises_clear_error_on_missing_access_token(
    mocker: MockerFixture,
) -> None:
    """A 200 response with no access_token field raises a clear error."""
    response = mocker.MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"error": "unexpected_shape"}
    mocker.patch(
        "workflow.openshell_oidc_token_provider.httpx.post", return_value=response
    )
    provider = OidcClientCredentialsTokenProvider(
        issuer="https://keycloak.example.com/realms/agents",
        client_id="my-client",
        client_secret="my-secret",
    )

    with pytest.raises(OidcTokenFetchError, match="no access_token"):
        provider.get_token()


def test_get_token_raises_clear_error_on_non_2xx_status(mocker: MockerFixture) -> None:
    """A non-2xx response (e.g. invalid_client) raises OidcTokenFetchError."""
    request = httpx.Request("POST", "https://keycloak.example.com/token")
    response = httpx.Response(401, request=request, json={"error": "invalid_client"})
    mocker.patch(
        "workflow.openshell_oidc_token_provider.httpx.post", return_value=response
    )
    provider = OidcClientCredentialsTokenProvider(
        issuer="https://keycloak.example.com/realms/agents",
        client_id="my-client",
        client_secret="my-secret",
    )

    with pytest.raises(OidcTokenFetchError, match="Failed to fetch OIDC access token"):
        provider.get_token()
