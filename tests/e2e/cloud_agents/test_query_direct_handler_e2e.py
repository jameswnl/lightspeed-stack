"""Handler-direct e2e tests for POST /v1/query/direct error paths.

Calls `query_direct_handler.__wrapped__(...)` directly, bypassing the
`@authorize` decorator and FastAPI routing/request-validation entirely.
Only the error paths are covered here (the happy path is exercised via
the spawn=none step-executor tests in test_step_executor_e2e.py, since
/v1/query/direct's happy path is a thin wrapper around the same
DirectExecutor those tests already cover).

Requires OPENAI_API_KEY (module-level config load only -- the error paths
themselves never reach the LLM).

Usage:
    uv run pytest tests/e2e/cloud_agents/test_query_direct_handler_e2e.py -v -s
"""

# pylint: disable=too-few-public-methods,unused-argument

from __future__ import annotations

import os
from typing import Any

import pytest
from fastapi import HTTPException

from app.endpoints.query_direct import QueryDirectRequest, query_direct_handler
from configuration import configuration

from .conftest import AUTH, make_request

pytestmark = pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="OPENAI_API_KEY not set",
)

_CONFIG = {
    "name": "e2e-query-direct-handler-test",
    "service": {
        "host": "localhost",
        "port": 8080,
        "auth_enabled": False,
        "workers": 1,
    },
    "llama_stack": {
        "use_as_library_client": False,
        "url": "http://localhost:8321",
    },
    "user_data_collection": {"feedback_enabled": False},
    "authentication": {"module": "noop"},
}


@pytest.fixture(name="e2e_config", scope="module")
def e2e_config_fixture() -> Any:
    """Load config for handler-direct tests. No Llama Stack needed."""
    configuration.init_from_dict(_CONFIG)
    return configuration


class TestQueryDirectErrorHandlingE2E:
    """E2E tests for /v1/query/direct error paths."""

    @pytest.mark.asyncio
    async def test_missing_provider_returns_400(self, e2e_config: Any) -> None:
        """Missing provider and model with no defaults returns 400."""
        body = QueryDirectRequest(query="Hello")

        with pytest.raises(HTTPException) as exc_info:
            await query_direct_handler.__wrapped__(make_request(), body, AUTH)
        assert exc_info.value.status_code == 400
        assert "Provider and model" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_prompt_too_long_returns_400(self, e2e_config: Any) -> None:
        """Oversized prompt returns 400."""
        body = QueryDirectRequest(
            query="x" * 200_000,
            provider="openai",
            model="gpt-4o-mini",
        )

        with pytest.raises(HTTPException) as exc_info:
            await query_direct_handler.__wrapped__(make_request(), body, AUTH)
        assert exc_info.value.status_code == 400
        assert "maximum length" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_unknown_mcp_server_returns_400(self, e2e_config: Any) -> None:
        """Unknown MCP server name returns 400."""
        body = QueryDirectRequest(
            query="Hello",
            provider="openai",
            model="gpt-4o-mini",
            mcp_servers=["nonexistent-server"],
        )

        with pytest.raises(HTTPException) as exc_info:
            await query_direct_handler.__wrapped__(make_request(), body, AUTH)
        assert exc_info.value.status_code == 400
        assert "Unknown MCP server" in str(exc_info.value.detail)
