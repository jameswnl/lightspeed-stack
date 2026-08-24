"""StepMiddleware implementations for lightspeed-stack.

Cross-cutting concerns that wrap executor calls via cloud-agents'
StepMiddleware protocol. Injected into ChatWorkflowRunner via the
middlewares parameter.
"""

from __future__ import annotations

from cloud_agents.workflow.executor.step.base import StepInput, StepResult
from opentelemetry import trace

from log import get_logger
from utils.otel_tracing import SpanAttributes, anonymize_value

logger = get_logger(__name__)


class TracingMiddleware:
    """OTEL tracing middleware — records attributes on the active span.

    Does NOT create spans. The MiddlewareExecutor (cloud-agents) creates
    the span; this middleware enriches it with lightspeed-stack-specific
    attributes using OTel semantic conventions.
    """

    async def before(self, step_input: StepInput) -> StepInput:
        """No-op before hook."""
        return step_input

    async def after(self, step_input: StepInput, result: StepResult) -> StepResult:
        """Record step metrics on the current span."""
        span = trace.get_current_span()
        if not span.is_recording():
            return result

        provider = step_input.provider or {}
        model_id = f"{provider.get('name', '')}:{provider.get('model', '')}"

        span.set_attribute(SpanAttributes.LLM_MODEL_ID, model_id)
        span.set_attribute(SpanAttributes.LLM_USAGE_INPUT_TOKENS, result.input_tokens)
        span.set_attribute(SpanAttributes.LLM_USAGE_OUTPUT_TOKENS, result.output_tokens)

        if step_input.metadata and step_input.metadata.user_id:
            span.set_attribute(
                SpanAttributes.USER_ID,
                anonymize_value(step_input.metadata.user_id),
            )

        return result


class AuditMiddleware:
    """Structured audit logging for every agent execution."""

    async def before(self, step_input: StepInput) -> StepInput:
        """Log step start."""
        user = step_input.metadata.user_id if step_input.metadata else "anonymous"
        logger.info(
            "Agent execution started: step=%s user=%s",
            step_input.step_name,
            user,
        )
        return step_input

    async def after(self, step_input: StepInput, result: StepResult) -> StepResult:
        """Log step completion with metrics."""
        user = step_input.metadata.user_id if step_input.metadata else "anonymous"
        logger.info(
            "Agent execution completed: step=%s user=%s status=%s "
            "tokens_in=%d tokens_out=%d duration_ms=%d",
            step_input.step_name,
            user,
            result.status,
            result.input_tokens,
            result.output_tokens,
            result.duration_ms,
        )
        return result


class QuotaMiddleware:
    """Token quota enforcement middleware.

    Placeholder implementation that logs quota checks. Will be wired
    to lightspeed-stack's quota infrastructure (src/quota/) when the
    quota path is decoupled from Llama Stack.
    """

    async def before(self, step_input: StepInput) -> StepInput:
        """Check quota before execution."""
        user = step_input.metadata.user_id if step_input.metadata else None
        if user:
            logger.debug("Quota check: user=%s (enforcement pending)", user)
        return step_input

    async def after(self, step_input: StepInput, result: StepResult) -> StepResult:
        """Deduct tokens after execution."""
        user = step_input.metadata.user_id if step_input.metadata else None
        if user and result.status == "completed":
            logger.debug(
                "Quota deduct: user=%s tokens=%d (enforcement pending)",
                user,
                result.input_tokens + result.output_tokens,
            )
        return result


def get_default_middleware() -> list:
    """Return the default middleware stack for lightspeed-stack.

    Order matters: outermost first. TracingMiddleware enriches the
    span created by cloud-agents' MiddlewareExecutor. AuditMiddleware
    logs before/after. QuotaMiddleware will enforce limits.

    Returns:
        List of middleware instances.
    """
    return [
        TracingMiddleware(),
        AuditMiddleware(),
        QuotaMiddleware(),
    ]
