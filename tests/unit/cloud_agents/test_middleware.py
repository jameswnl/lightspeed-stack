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
        """After hook sets OTEL attributes using semantic conventions."""
        mock_span = mocker.MagicMock()
        mock_span.is_recording.return_value = True
        mocker.patch(
            "workflow.middleware.trace.get_current_span",
            return_value=mock_span,
        )
        mocker.patch(
            "workflow.middleware.anonymize_value",
            side_effect=lambda x: f"anon:{x}",
        )

        mw = TracingMiddleware()
        step_input = _make_step_input(mocker)
        result = _make_result(mocker)

        returned = await mw.after(step_input, result)

        assert returned is result
        mock_span.set_attribute.assert_any_call("llm.model.id", "openai:gpt-4o-mini")
        mock_span.set_attribute.assert_any_call("llm.usage.input_tokens", 50)
        mock_span.set_attribute.assert_any_call("llm.usage.output_tokens", 25)
        mock_span.set_attribute.assert_any_call("user.id", "anon:test-user")

    @pytest.mark.asyncio
    async def test_after_noop_when_not_recording(self, mocker: MockerFixture) -> None:
        """No-op when span is not recording."""
        mock_span = mocker.MagicMock()
        mock_span.is_recording.return_value = False
        mocker.patch(
            "workflow.middleware.trace.get_current_span",
            return_value=mock_span,
        )

        mw = TracingMiddleware()
        await mw.after(_make_step_input(mocker), _make_result(mocker))

        mock_span.set_attribute.assert_not_called()


class TestAuditMiddleware:
    """Tests for AuditMiddleware."""

    @pytest.mark.asyncio
    async def test_passes_through(self, mocker: MockerFixture) -> None:
        """Audit middleware passes input and result through."""
        mw = AuditMiddleware()
        step_input = _make_step_input(mocker)
        result = _make_result(mocker)

        assert await mw.before(step_input) is step_input
        assert await mw.after(step_input, result) is result


class TestQuotaMiddleware:
    """Tests for QuotaMiddleware."""

    @pytest.mark.asyncio
    async def test_passes_through(self, mocker: MockerFixture) -> None:
        """Quota middleware passes input and result through."""
        mw = QuotaMiddleware()
        step_input = _make_step_input(mocker)
        result = _make_result(mocker)

        assert await mw.before(step_input) is step_input
        assert await mw.after(step_input, result) is result


class TestGetDefaultMiddleware:
    """Tests for get_default_middleware."""

    def test_returns_three_middlewares(self) -> None:
        """Default stack has tracing, audit, quota."""
        middlewares = get_default_middleware()
        assert len(middlewares) == 3
        assert isinstance(middlewares[0], TracingMiddleware)
        assert isinstance(middlewares[1], AuditMiddleware)
        assert isinstance(middlewares[2], QuotaMiddleware)
