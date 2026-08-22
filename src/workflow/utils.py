"""Workflow utility functions.

Provides helpers for building agent parameters from workflow step
specifications, bridging cloud-agents' step model to lightspeed-stack's
ResponsesApiParams.
"""

from typing import Any, Optional

from models.common.responses.responses_api_params import ResponsesApiParams


def prepare_workflow_step_params(  # pylint: disable=too-many-arguments
    *,
    model: str,
    prompt: str,
    instructions: Optional[str] = None,
    tools: Optional[list[Any]] = None,
    max_infer_iters: Optional[int] = None,
    max_tool_calls: Optional[int] = None,
    conversation_id: Optional[str] = None,
) -> ResponsesApiParams:
    """Build ResponsesApiParams from workflow step parameters.

    Simplified version of prepare_responses_params() that works without
    an HTTP request context. Skips conversation management, MCP header
    propagation, and RAG context assembly.

    Parameters:
        model: Full model ID in "provider/model" format.
        prompt: The interpolated prompt for this step.
        instructions: System prompt / instructions for the agent.
        tools: Tool definitions (MCP servers, file_search, etc.).
        max_infer_iters: Maximum inference iterations.
        max_tool_calls: Maximum tool calls.
        conversation_id: Optional conversation ID for multi-turn context.

    Returns:
        ResponsesApiParams ready for build_agent().
    """
    return ResponsesApiParams(
        input=prompt,
        model=model,
        conversation=conversation_id or "",
        instructions=instructions,
        tools=tools,
        max_infer_iters=max_infer_iters,
        max_tool_calls=max_tool_calls,
        store=False,
        stream=False,
    )
