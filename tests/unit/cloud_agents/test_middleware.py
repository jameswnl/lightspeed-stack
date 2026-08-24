"""Unit tests for lightspeed-stack StepMiddleware implementations."""

from __future__ import annotations

import pytest
from pytest_mock import MockerFixture

from workflow.middleware import (
    AuditMiddleware,
    QuotaMiddleware,
    TracingMiddleware,
    get_default_middleware,
)


def _make_step_input(mocker: MockerFixture, user_id: str = "test-user") -> object:
    """Create a mock StepInput."""
    step_input = mocker.MagicMock()
    step_input.step_name = "test-step"
    step_input.provider = {"name": "openai", "model": "gpt-4o-mini"}
    step_input.metadata = mocker.MagicMock()
    step_input.metadata.user_id = user_id
    return step_input


def _make_result(mocker: MockerFixture) -> object:
    """Create a mock StepResult."""
    result = mocker.MagicMock()
    result.status = "completed"
    result.input_tokens = 50
    result.output_tokens = 25
    result.duration_ms = 500
    return result


class TestTracingMiddleware:
    """Tests for TracingMiddleware."""

    @pytest.mark.asyncio
    async def test_before_passes_through(self, mocker: MockerFixture) -> None:
        """Before hook returns step_input unchanged."""
        mw = TracingMiddleware()
        step_input = _make_step_input(mocker)
        result = await mw.before(step_input)
        assert result is step_input

    @pytest.mark.asyncio
    async def test_after_sets_span_attributes(self, mocker: MockerFixture) -> None:
        """After hook sets OTEL attributes on current span."""
        mock_span = mocker.MagicMock()
        mock_span.is_recording.return_value = True
        mocker.patch(
            "workflow.middleware.trace.get_current_span",
            return_value=mock_span,
        )

        mw = TracingMiddleware()
        step_input = _make_step_input(mocker)
        result = _make_result(mocker)

        await mw.after(step_input, result)

        mock_span.set_attribute.assert_any_call("step.name", "test-step")
        mock_span.set_attribute.assert_any_call("step.status", "completed")
        mock_span.set_attribute.assert_any_call("llm.usage.input_tokens", 50)
        mock_span.set_attribute.assert_any_call("llm.usage.output_tokens", 25)
        mock_span.set_attribute.assert_any_call("user.id", "test-user")


class TestAuditMiddleware:
    """Tests for AuditMiddleware."""

    @pytest.mark.asyncio
    async def test_logs_before_and_after(self, mocker: MockerFixture) -> None:
        """Audit middleware logs start and completion."""
        mw = AuditMiddleware()
        step_input = _make_step_input(mocker)
        result = _make_result(mocker)

        returned_input = await mw.before(step_input)
        assert returned_input is step_input

        returned_result = await mw.after(step_input, result)
        assert returned_result is result


class TestQuotaMiddleware:
    """Tests for QuotaMiddleware."""

    @pytest.mark.asyncio
    async def test_passes_through(self, mocker: MockerFixture) -> None:
        """Quota middleware passes through (placeholder)."""
        mw = QuotaMiddleware()
        step_input = _make_step_input(mocker)
        result = _make_result(mocker)

        returned_input = await mw.before(step_input)
        assert returned_input is step_input

        returned_result = await mw.after(step_input, result)
        assert returned_result is result


class TestGetDefaultMiddleware:
    """Tests for get_default_middleware."""

    def test_returns_three_middlewares(self) -> None:
        """Default stack has tracing, audit, quota."""
        middlewares = get_default_middleware()
        assert len(middlewares) == 3
        assert isinstance(middlewares[0], TracingMiddleware)
        assert isinstance(middlewares[1], AuditMiddleware)
        assert isinstance(middlewares[2], QuotaMiddleware)
