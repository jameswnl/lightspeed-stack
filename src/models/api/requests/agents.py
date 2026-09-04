"""Request models for agent and workflow endpoints."""

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator


class AgentRunRequest(BaseModel):
    """Request body for POST /v1/agents/run.

    All agent parameters are passed inline — no registry lookup.

    Attributes:
        prompt: The task prompt for the agent.
        instructions: System prompt / instructions.
        model: Full model ID (e.g. "openai/gpt-4o").
        provider: Provider name (used with model if no slash in model).
        spawn: Execution mode.
        sandbox_image: Container image for spawn=ephemeral.
        tools: Tool definitions.
        mcp_servers: MCP server names.
        output_schema: JSON Schema for structured output.
        context: Prior context (e.g. from previous steps).
    """

    prompt: str = Field(
        ...,
        description="The task prompt for the agent.",
    )

    instructions: Optional[str] = Field(
        None,
        description="System prompt / instructions for the agent.",
    )

    model: Optional[str] = Field(
        None,
        description="Full model ID (e.g. 'openai/gpt-4o'). "
        "Falls back to inference.default_model.",
    )

    provider: Optional[str] = Field(
        None,
        description="Provider name. Used to prefix model if no slash present. "
        "Falls back to inference.default_provider.",
    )

    spawn: Literal["none", "local", "ephemeral"] = Field(
        "none",
        description="'none' runs in-process; 'local' spawns a subprocess; "
        "'ephemeral' spawns a container.",
    )

    sandbox_image: Optional[str] = Field(
        None,
        description="Container image for spawn=ephemeral.",
    )

    tools: list[str] = Field(
        default_factory=list,
        description="Registered tool names for the agent to use.",
    )

    mcp_servers: Optional[list[dict[str, Any]]] = Field(
        None,
        description="MCP server configs: [{name, url, headers}]. "
        "Each server is connected via pydantic-ai MCPToolset.",
    )

    output_schema: Optional[dict[str, Any]] = Field(
        None,
        description="JSON Schema for structured output.",
    )

    context: Optional[dict[str, Any]] = Field(
        None,
        description="Prior context from previous steps or calls.",
    )


class RunWorkflowRequest(BaseModel):
    """Request body for POST /v1/workflows/run.

    Attributes:
        definition: Workflow definition (same schema as cloud-agents YAML).
        provider: Default LLM provider config for all steps.
        sandbox_image: Default sandbox image for ephemeral steps.
        approval_policy: Optional approval policy.
        session_id: Optional caller-provided ID grouping related workflow runs.
        mcp_servers: Run-scoped MCP server configs available to steps.
    """

    definition: dict[str, Any] = Field(
        ...,
        description="Workflow definition with apiVersion, kind, metadata, spec.",
    )

    provider: Optional[dict[str, Any]] = Field(
        None,
        description="Default provider config: {name, model, credentials_secret}.",
    )

    mcp_servers: Optional[list[dict[str, Any]]] = Field(
        None,
        description="Run-scoped MCP server configs: [{name, url, headers}]. "
        "Made available to the workflow's steps; spawn:none agent steps "
        "connect to them via pydantic-ai MCPToolset.",
    )

    sandbox_image: Optional[str] = Field(
        None,
        description="Default sandbox image for ephemeral steps.",
    )

    approval_policy: Optional[dict[str, Any]] = Field(
        None,
        description="Approval policy for human-approval steps.",
    )

    session_id: Optional[str] = Field(
        None,
        description="Caller-provided ID grouping related workflow runs.",
    )

    @field_validator("session_id")
    @classmethod
    def normalize_session_id(cls, value: Optional[str]) -> Optional[str]:
        """Treat an empty string session_id the same as omitted.

        Parameters:
            value: Raw session_id from the request; may be None or empty.

        Returns:
            None if the value is empty, otherwise the original value.
        """
        return value or None


class ApproveWorkflowRequest(BaseModel):
    """Request body for POST /v1/workflows/{id}/approve.

    Attributes:
        step_name: Name of the approval step.
        decision: Approval decision.
        approver: Identity of the approver.
    """

    step_name: str = Field(
        ...,
        description="Name of the approval step to approve.",
    )

    decision: Literal["approved", "rejected"] = Field(
        ...,
        description="Approval decision.",
    )

    approver: str = Field(
        "",
        description="Identity of the approver.",
    )
