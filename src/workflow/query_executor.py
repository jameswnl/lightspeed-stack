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
    StepMetadata,
    StepResult,
    StreamEvent,
)
from cloud_agents.workflow.executor.step.conversation import ConversationMessage
from cloud_agents.workflow.executor.step.dispatch import get_step_executor

from configuration import configuration
from log import get_logger
from workflow.storage import WorkflowStorageFactory

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


def _validate_and_build_step_input(  # pylint: disable=too-many-arguments
    *,
    prompt: str,
    model: Optional[str],
    provider: Optional[str],
    instructions: Optional[str],
    mcp_server_names: Optional[list[str]],
    output_schema: Optional[dict[str, Any]],
    context: Optional[dict[str, Any]],
    step_name: str,
    user_id: str = "",
    session_id: str = "",
) -> tuple[StepInput, str, str]:
    """Validate inputs and build a StepInput.

    Shared by both blocking and streaming execution paths.

    Parameters:
        prompt: User's query text.
        model: Model name.
        provider: Provider name.
        instructions: System prompt.
        mcp_server_names: MCP server names to resolve.
        output_schema: Structured output schema.
        context: Prior context.
        step_name: Step name for logging.

    Returns:
        Tuple of (StepInput, resolved_provider, resolved_model).

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

    metadata = StepMetadata(user_id=user_id or None, session_id=session_id or None)

    step_input = StepInput(
        prompt=prompt,
        provider={"name": provider_name, "model": model_name},
        system_prompt=instructions,
        output_schema=output_schema,
        mcp_servers=mcp_servers or None,
        context=context or {},
        step_name=step_name,
        output_key="response",
        metadata=metadata,
    )

    return step_input, provider_name, model_name


async def _load_conversation_context(
    conversation_id: str,
) -> dict[str, Any]:
    """Load prior conversation turns as context for the executor.

    Parameters:
        conversation_id: Conversation/workflow ID to load turns from.

    Returns:
        Context dict with prior turns, or empty dict if unavailable.
    """
    try:
        store = WorkflowStorageFactory.get_transcript_store()
    except RuntimeError:
        return {}

    try:
        turns = await store.load_recent_turns(conversation_id, limit=20)
        if not turns:
            return {}

        history: list[dict[str, Any]] = []
        for turn in turns:
            messages = turn.get("messages", [])
            for msg in messages:
                entry = (
                    msg
                    if isinstance(msg, dict)
                    else (msg.to_dict() if hasattr(msg, "to_dict") else None)
                )
                if entry and "role" in entry and "content" in entry:
                    history.append(entry)

        return {"conversation_history": history} if history else {}
    except Exception as exc:
        logger.warning("Failed to load conversation context: %s", exc)
        return {}


async def _save_conversation_turn(
    conversation_id: str,
    prompt: str,
    result: StepResult,
) -> None:
    """Save a conversation turn to the transcript store.

    Parameters:
        conversation_id: Conversation/workflow ID.
        prompt: User's prompt for this turn.
        result: Executor result for this turn.
    """
    try:
        store = WorkflowStorageFactory.get_transcript_store()
    except RuntimeError:
        return

    from cloud_agents.workflow.core.models import StepTranscript, TranscriptEvent

    response_text = ""
    if isinstance(result.output, dict):
        response_text = result.output.get("response", str(result.output))
    elif result.output is not None:
        response_text = str(result.output)

    messages = [
        ConversationMessage(role="user", content=prompt),
        ConversationMessage(role="assistant", content=response_text),
    ]

    import uuid

    turn_id = f"turn-{uuid.uuid4().hex[:8]}"
    transcript = StepTranscript(
        step_name=turn_id,
        events=[TranscriptEvent(ts="", type="result", data={"text": response_text})],
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        duration_ms=result.duration_ms,
    )

    try:
        await store.save(
            workflow_id=conversation_id,
            step_name=turn_id,
            transcript=transcript,
            messages=messages,
        )
    except Exception as exc:
        logger.warning("Failed to save conversation turn: %s", exc)


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
    """Execute a query using cloud-agents' DirectExecutor (blocking).

    Parameters:
        prompt: User's query text.
        model: Model name (e.g. "gpt-4o-mini").
        provider: Provider name (e.g. "openai").
        instructions: System prompt / instructions.
        mcp_server_names: MCP server names to include (None = all).
        output_schema: Optional structured output schema.
        context: Prior conversation context.
        conversation_id: Optional conversation ID for multi-turn.
        user_id: User identifier for audit logging.
        username: Username for audit logging.

    Returns:
        StepResult with agent response, transcript, and metrics.

    Raises:
        ValueError: On validation failure.
    """
    merged_context = dict(context or {})
    if conversation_id:
        conv_context = await _load_conversation_context(conversation_id)
        merged_context.update(conv_context)

    step_input, provider_name, model_name = _validate_and_build_step_input(
        prompt=prompt,
        model=model,
        provider=provider,
        instructions=instructions,
        mcp_server_names=mcp_server_names,
        output_schema=output_schema,
        context=merged_context,
        step_name="query",
        user_id=user_id,
        session_id=conversation_id or "",
    )

    executor = get_step_executor(_QUERY_STEP_DEF, spawner=None)
    user_label = username or user_id or "anonymous"

    logger.info(
        "Query via DirectExecutor: user=%s model=%s:%s mcp_servers=%d",
        user_label,
        provider_name,
        model_name,
        len(step_input.mcp_servers or []),
    )

    result = await executor.run(step_input)

    logger.info(
        "Query completed: user=%s status=%s duration_ms=%d tokens_in=%d tokens_out=%d",
        user_label,
        result.status,
        result.duration_ms,
        result.input_tokens,
        result.output_tokens,
    )

    if conversation_id and result.status == "completed":
        await _save_conversation_turn(conversation_id, prompt, result)

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

    When tools or MCP servers are configured, yields real token-by-token
    events via pydantic-ai Agent.run_stream(). Without tools, falls back
    to a single complete event (no intermediate token events).

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
        StreamEvent instances. Token-by-token when tools/MCP are
        configured; single complete event otherwise.

    Raises:
        ValueError: On validation failure (raised BEFORE streaming starts).
    """
    step_input, provider_name, model_name = _validate_and_build_step_input(
        prompt=prompt,
        model=model,
        provider=provider,
        instructions=instructions,
        mcp_server_names=mcp_server_names,
        output_schema=output_schema,
        context=context,
        step_name="query-stream",
        user_id=user_id,
    )

    executor = get_step_executor(_QUERY_STEP_DEF, spawner=None)
    user_label = username or user_id or "anonymous"

    logger.info(
        "Streaming query via DirectExecutor: user=%s model=%s:%s mcp_servers=%d",
        user_label,
        provider_name,
        model_name,
        len(step_input.mcp_servers or []),
    )

    async for event in executor.run_stream(step_input):
        yield event

    logger.info("Stream completed: user=%s", user_label)
