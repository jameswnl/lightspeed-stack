"""StepMiddleware implementations for lightspeed-stack.

Cross-cutting concerns that wrap executor calls: OTEL tracing,
quota enforcement, and audit logging.
"""

from __future__ import annotations

from typing import Any

from cloud_agents.workflow.executor.step.base import StepInput, StepResult
from opentelemetry import trace

from log import get_logger

logger = get_logger(__name__)

_tracer = trace.get_tracer("lightspeed_stack.workflow")


class TracingMiddleware:
    """OTEL tracing middleware — creates spans around executor calls."""

    async def before(self, step_input: StepInput) -> StepInput:
        """Log step start."""
        return step_input

    async def after(self, step_input: StepInput, result: StepResult) -> StepResult:
        """Record step metrics on the current span."""
        span = trace.get_current_span()
        if span.is_recording():
            span.set_attribute("step.name", step_input.step_name)
            span.set_attribute("step.status", result.status)
            span.set_attribute("llm.usage.input_tokens", result.input_tokens)
            span.set_attribute("llm.usage.output_tokens", result.output_tokens)
            span.set_attribute("llm.duration_ms", result.duration_ms)
            if step_input.metadata and step_input.metadata.user_id:
                span.set_attribute("user.id", step_input.metadata.user_id)
            provider = step_input.provider
            if provider:
                span.set_attribute(
                    "llm.model",
                    f"{provider.get('name', '')}:{provider.get('model', '')}",
                )
        return result


class AuditMiddleware:
    """Audit logging middleware — structured log for every execution."""

    async def before(self, step_input: StepInput) -> StepInput:
        """Log step start."""
        user = step_input.metadata.user_id if step_input.metadata else "anonymous"
        logger.info(
            "Agent step started: step=%s user=%s",
            step_input.step_name,
            user,
        )
        return step_input

    async def after(self, step_input: StepInput, result: StepResult) -> StepResult:
        """Log step completion with metrics."""
        user = step_input.metadata.user_id if step_input.metadata else "anonymous"
        logger.info(
            "Agent step completed: step=%s user=%s status=%s "
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
    """Quota enforcement middleware — checks token availability.

    Placeholder: checks are logged but not enforced until the
    quota infrastructure is wired to the workflow path.
    """

    async def before(self, step_input: StepInput) -> StepInput:
        """Check quota before execution (placeholder)."""
        user = step_input.metadata.user_id if step_input.metadata else None
        if user:
            logger.debug("Quota check: user=%s (not enforced yet)", user)
        return step_input

    async def after(self, step_input: StepInput, result: StepResult) -> StepResult:
        """Deduct tokens after execution (placeholder)."""
        user = step_input.metadata.user_id if step_input.metadata else None
        if user and result.status == "completed":
            logger.debug(
                "Quota deduct: user=%s tokens=%d (not enforced yet)",
                user,
                result.input_tokens + result.output_tokens,
            )
        return result


def get_default_middleware() -> list[Any]:
    """Return the default middleware stack for lightspeed-stack.

    Returns:
        List of middleware instances, outermost first.
    """
    return [
        TracingMiddleware(),
        AuditMiddleware(),
        QuotaMiddleware(),
    ]
