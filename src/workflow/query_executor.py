"""Bridge between /query endpoint and cloud-agents' DirectExecutor.

Resolves lightspeed-stack's MCP server config into the format
DirectExecutor expects, then delegates agent execution. This module
is the migration path from build_agent() → Llama Stack to
DirectExecutor → pydantic-ai for the /query chat agent.
"""

from __future__ import annotations

from typing import Any, Optional

from cloud_agents.workflow.executor.step.base import StepInput, StepResult
from cloud_agents.workflow.executor.step.dispatch import get_step_executor

from configuration import configuration
from log import get_logger

logger = get_logger(__name__)


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
    """
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

    Returns:
        StepResult with agent response, transcript, and metrics.
    """
    provider_name = provider or ""
    model_name = model or ""
    if not provider_name or not model_name:
        inference = configuration.inference
        provider_name = provider_name or inference.default_provider or ""
        model_name = model_name or inference.default_model or ""

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

    step_def = {"name": "query", "spawn": "none"}
    executor = get_step_executor(step_def, spawner=None)

    logger.info(
        "Executing query via DirectExecutor (model=%s:%s, mcp_servers=%d)",
        provider_name,
        model_name,
        len(mcp_servers),
    )

    return await executor.run(step_input)
