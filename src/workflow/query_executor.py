"""Bridge between /query endpoint and cloud-agents' DirectExecutor.

Resolves lightspeed-stack's MCP server config into the format
DirectExecutor expects, then delegates agent execution. This module
is the migration path from build_agent() → Llama Stack to
DirectExecutor → pydantic-ai for the /query chat agent.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Optional

from cloud_agents.workflow.executor.step.base import (
    StepInput,
    StepResult,
    StreamEvent,
)
from cloud_agents.workflow.executor.step.dispatch import get_step_executor

from configuration import configuration
from log import get_logger

logger = get_logger(__name__)

_QUERY_STEP_DEF: dict[str, str] = {"name": "query", "spawn": "none"}

MAX_PROMPT_LENGTH = 100_000
MAX_INSTRUCTIONS_LENGTH = 50_000


def resolve_mcp_servers(
    server_names: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    """Resolve MCP server names to configs for DirectExecutor.

    Reads from lightspeed-stack's configuration and converts
    ModelContextProtocolServer objects to the dict format
    DirectExecutor expects: {name, url, headers}.

    Parameters:
        server_names: Optional list of server names to resolve.
            If None, all configured servers are included.

    Returns:
        List of MCP server config dicts.

    Raises:
        ValueError: If a requested server name is not configured.
    """
    configured = {s.name: s for s in configuration.mcp_servers}

    if server_names:
        unknown = [n for n in server_names if n not in configured]
        if unknown:
            raise ValueError(
                f"Unknown MCP server(s): {unknown}. "
                f"Configured: {sorted(configured.keys())}"
            )

    mcp_configs: list[dict[str, Any]] = []
    for server in configuration.mcp_servers:
        if server_names and server.name not in server_names:
            continue
        config: dict[str, Any] = {
            "name": server.name,
            "url": server.url,
        }
        if server.resolved_authorization_headers:
            config["headers"] = dict(server.resolved_authorization_headers)
        mcp_configs.append(config)
    return mcp_configs


async def execute_query_via_direct_executor(
    *,
    prompt: str,
    model: Optional[str] = None,
    provider: Optional[str] = None,
    instructions: Optional[str] = None,
    mcp_server_names: Optional[list[str]] = None,
    output_schema: Optional[dict[str, Any]] = None,
    context: Optional[dict[str, Any]] = None,
    user_id: str = "",
    username: str = "",
) -> StepResult:
    """Execute a query using cloud-agents' DirectExecutor.

    This is the migration path for /query — same capabilities as
    build_agent() but using DirectExecutor → pydantic-ai directly,
    no Llama Stack in the path.

    Parameters:
        prompt: User's query text.
        model: Model name (e.g. "gpt-4o-mini").
        provider: Provider name (e.g. "openai").
        instructions: System prompt / instructions.
        mcp_server_names: MCP server names to include (None = all).
        output_schema: Optional structured output schema.
        context: Prior conversation context.
        user_id: User identifier for audit logging.
        username: Username for audit logging.

    Returns:
        StepResult with agent response, transcript, and metrics.

    Raises:
        ValueError: If provider/model are not specified and no defaults
            are configured, or if prompt exceeds length limits.
    """
    if len(prompt) > MAX_PROMPT_LENGTH:
        raise ValueError(
            f"Prompt exceeds maximum length ({len(prompt)} > {MAX_PROMPT_LENGTH})"
        )
    if instructions and len(instructions) > MAX_INSTRUCTIONS_LENGTH:
        raise ValueError(
            f"Instructions exceed maximum length "
            f"({len(instructions)} > {MAX_INSTRUCTIONS_LENGTH})"
        )

    provider_name = provider or ""
    model_name = model or ""
    if not provider_name or not model_name:
        inference = configuration.inference
        provider_name = provider_name or inference.default_provider or ""
        model_name = model_name or inference.default_model or ""

    if not provider_name or not model_name:
        raise ValueError(
            "Provider and model must be specified or configured as defaults "
            "(inference.default_provider / inference.default_model)"
        )

    mcp_servers = resolve_mcp_servers(mcp_server_names)

    step_input = StepInput(
        prompt=prompt,
        provider={"name": provider_name, "model": model_name},
        system_prompt=instructions,
        output_schema=output_schema,
        mcp_servers=mcp_servers or None,
        context=context or {},
        step_name="query",
        output_key="response",
    )

    executor = get_step_executor(_QUERY_STEP_DEF, spawner=None)

    logger.info(
        "Query via DirectExecutor: user=%s model=%s:%s mcp_servers=%d",
        username or user_id or "anonymous",
        provider_name,
        model_name,
        len(mcp_servers),
    )

    result = await executor.run(step_input)

    logger.info(
        "Query completed: user=%s status=%s duration_ms=%d tokens_in=%d tokens_out=%d",
        username or user_id or "anonymous",
        result.status,
        result.duration_ms,
        result.input_tokens,
        result.output_tokens,
    )

    return result


async def stream_query_via_direct_executor(
    *,
    prompt: str,
    model: Optional[str] = None,
    provider: Optional[str] = None,
    instructions: Optional[str] = None,
    mcp_server_names: Optional[list[str]] = None,
    output_schema: Optional[dict[str, Any]] = None,
    context: Optional[dict[str, Any]] = None,
    user_id: str = "",
    username: str = "",
) -> AsyncIterator[StreamEvent]:
    """Stream a query using cloud-agents' DirectExecutor.

    Same as execute_query_via_direct_executor but streams token events
    as they arrive instead of waiting for the complete result.

    Parameters:
        prompt: User's query text.
        model: Model name (e.g. "gpt-4o-mini").
        provider: Provider name (e.g. "openai").
        instructions: System prompt / instructions.
        mcp_server_names: MCP server names to include (None = all).
        output_schema: Optional structured output schema.
        context: Prior conversation context.
        user_id: User identifier for audit logging.
        username: Username for audit logging.

    Yields:
        StreamEvent instances (type="token" for deltas, "complete" for final result).

    Raises:
        ValueError: If provider/model are not specified and no defaults
            are configured, or if prompt exceeds length limits.
    """
    if len(prompt) > MAX_PROMPT_LENGTH:
        raise ValueError(
            f"Prompt exceeds maximum length ({len(prompt)} > {MAX_PROMPT_LENGTH})"
        )
    if instructions and len(instructions) > MAX_INSTRUCTIONS_LENGTH:
        raise ValueError(
            f"Instructions exceed maximum length "
            f"({len(instructions)} > {MAX_INSTRUCTIONS_LENGTH})"
        )

    provider_name = provider or ""
    model_name = model or ""
    if not provider_name or not model_name:
        inference = configuration.inference
        provider_name = provider_name or inference.default_provider or ""
        model_name = model_name or inference.default_model or ""

    if not provider_name or not model_name:
        raise ValueError(
            "Provider and model must be specified or configured as defaults "
            "(inference.default_provider / inference.default_model)"
        )

    mcp_servers = resolve_mcp_servers(mcp_server_names)

    step_input = StepInput(
        prompt=prompt,
        provider={"name": provider_name, "model": model_name},
        system_prompt=instructions,
        output_schema=output_schema,
        mcp_servers=mcp_servers or None,
        context=context or {},
        step_name="query-stream",
        output_key="response",
    )

    executor = get_step_executor(_QUERY_STEP_DEF, spawner=None)

    logger.info(
        "Streaming query via DirectExecutor: user=%s model=%s:%s mcp_servers=%d",
        username or user_id or "anonymous",
        provider_name,
        model_name,
        len(mcp_servers),
    )

    async for event in executor.run_stream(step_input):
        yield event

    logger.info(
        "Stream completed: user=%s",
        username or user_id or "anonymous",
    )
