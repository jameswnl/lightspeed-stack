"""Bridge between /query endpoint and cloud-agents' ChatWorkflowRunner.

Manages a ChatWorkflowRunner instance that handles multi-turn
conversation state, middleware, and executor dispatch. This module
is the migration path from build_agent() → Llama Stack to
ChatWorkflowRunner → pydantic-ai for the /query chat agent.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Optional

from cloud_agents.workflow.executor.chat.runner import (
    ChatWorkflowConfig,
    ChatWorkflowRunner,
)
from cloud_agents.workflow.executor.step.base import (
    StepResult,
    StreamEvent,
)

from configuration import configuration
from log import get_logger
from workflow.storage import WorkflowStorageFactory

logger = get_logger(__name__)

MAX_PROMPT_LENGTH = 100_000
MAX_INSTRUCTIONS_LENGTH = 50_000

_runner: Optional[ChatWorkflowRunner] = None


def resolve_mcp_servers(
    server_names: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    """Resolve MCP server names to configs for the executor.

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


def _validate_prompt(prompt: str, instructions: Optional[str] = None) -> None:
    """Validate prompt and instructions length.

    Raises:
        ValueError: On validation failure.
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


def _resolve_provider(provider: Optional[str], model: Optional[str]) -> dict[str, str]:
    """Resolve provider and model, falling back to config defaults.

    Returns:
        Dict with 'name' and 'model' keys.

    Raises:
        ValueError: If neither explicit nor default values available.
    """
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
    return {"name": provider_name, "model": model_name}


def _get_or_create_runner(
    *,
    provider: dict[str, str],
    instructions: Optional[str] = None,
    mcp_server_names: Optional[list[str]] = None,
) -> ChatWorkflowRunner:
    """Get or create the ChatWorkflowRunner singleton.

    Parameters:
        provider: Resolved provider dict.
        instructions: System prompt.
        mcp_server_names: MCP server names to resolve.

    Returns:
        ChatWorkflowRunner instance.
    """
    global _runner

    if _runner is not None:
        return _runner

    mcp_servers = resolve_mcp_servers(mcp_server_names)

    from utils.prompts import (
        get_system_prompt,
    )  # pylint: disable=import-outside-toplevel

    resolved_instructions = get_system_prompt(instructions)

    config = ChatWorkflowConfig(
        provider=provider,
        system_prompt=resolved_instructions,
        mcp_servers=mcp_servers or None,
    )

    try:
        run_store = WorkflowStorageFactory.get_run_state_store()
        transcript_store = WorkflowStorageFactory.get_transcript_store()
    except RuntimeError:
        run_store = None
        transcript_store = None

    from workflow.middleware import (
        get_default_middleware,
    )  # pylint: disable=import-outside-toplevel

    _runner = ChatWorkflowRunner(
        run_store=run_store,
        transcript_store=transcript_store,
        config=config,
        middlewares=get_default_middleware(),
    )
    return _runner


def reset_runner() -> None:
    """Reset the runner singleton (for testing)."""
    global _runner
    _runner = None


async def execute_query_via_direct_executor(  # pylint: disable=too-many-arguments
    *,
    prompt: str,
    model: Optional[str] = None,
    provider: Optional[str] = None,
    instructions: Optional[str] = None,
    mcp_server_names: Optional[list[str]] = None,
    output_schema: Optional[dict[str, Any]] = None,
    context: Optional[dict[str, Any]] = None,
    conversation_id: Optional[str] = None,
    user_id: str = "",
    username: str = "",
) -> StepResult:
    """Execute a query using ChatWorkflowRunner (blocking).

    Parameters:
        prompt: User's query text.
        model: Model name (e.g. "gpt-4o-mini").
        provider: Provider name (e.g. "openai").
        instructions: System prompt / instructions.
        mcp_server_names: MCP server names to include (None = all).
        output_schema: Optional structured output schema.
        context: Prior conversation context.
        conversation_id: Conversation ID for multi-turn.
        user_id: User identifier for audit logging.
        username: Username for audit logging.

    Returns:
        StepResult with agent response, transcript, and metrics.

    Raises:
        ValueError: On validation failure.
    """
    _validate_prompt(prompt, instructions)
    if output_schema:
        raise ValueError("output_schema is not yet supported via ChatWorkflowRunner")
    resolved_provider = _resolve_provider(provider, model)
    user_label = username or user_id or "anonymous"

    runner = _get_or_create_runner(
        provider=resolved_provider,
        instructions=instructions,
        mcp_server_names=mcp_server_names,
    )

    if not conversation_id:
        conversation_id = await runner.start({"user_id": user_id or None})

    logger.info(
        "Query via ChatWorkflowRunner: user=%s conv=%s model=%s:%s",
        user_label,
        conversation_id,
        resolved_provider["name"],
        resolved_provider["model"],
    )

    result = await runner.send_message(conversation_id, prompt)

    logger.info(
        "Query completed: user=%s conv=%s status=%s duration_ms=%d "
        "tokens_in=%d tokens_out=%d",
        user_label,
        conversation_id,
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
    conversation_id: Optional[str] = None,
    user_id: str = "",
    username: str = "",
) -> AsyncIterator[StreamEvent]:
    """Stream a query using ChatWorkflowRunner.

    Parameters:
        prompt: User's query text.
        model: Model name (e.g. "gpt-4o-mini").
        provider: Provider name (e.g. "openai").
        instructions: System prompt / instructions.
        mcp_server_names: MCP server names to include (None = all).
        output_schema: Not yet supported — raises ValueError if set.
        context: Prior conversation context.
        conversation_id: Conversation ID for multi-turn.
        user_id: User identifier for audit logging.
        username: Username for audit logging.

    Yields:
        StreamEvent instances.

    Raises:
        ValueError: On validation failure (raised BEFORE streaming starts).
    """
    _validate_prompt(prompt, instructions)
    if output_schema:
        raise ValueError(
            "output_schema is not yet supported via ChatWorkflowRunner streaming"
        )
    resolved_provider = _resolve_provider(provider, model)
    user_label = username or user_id or "anonymous"

    runner = _get_or_create_runner(
        provider=resolved_provider,
        instructions=instructions,
        mcp_server_names=mcp_server_names,
    )

    if not conversation_id:
        conversation_id = await runner.start({"user_id": user_id or None})

    logger.info(
        "Streaming query via ChatWorkflowRunner: user=%s conv=%s model=%s:%s",
        user_label,
        conversation_id,
        resolved_provider["name"],
        resolved_provider["model"],
    )

    async for event in runner.send_message_stream(conversation_id, prompt):
        yield event

    logger.info("Stream completed: user=%s conv=%s", user_label, conversation_id)
