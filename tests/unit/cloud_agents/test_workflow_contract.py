"""Run examples/workflows/cases.yaml against the real handler and config (issue #51).

The cases are the acceptance contract for the #51 phases. Each case carries the
phase that makes it real; cases for later phases are skipped, so raising
``IMPLEMENTED_PHASE`` (and removing the matching part of the example's
``reference_gate.py``) is how a phase is declared done.

Only the HTTP status is compared here: the reference gate's ``reason`` strings
are specific to the reference implementation.
"""

# pyright: reportMissingImports=false
# pylint: disable=import-outside-toplevel,redefined-outer-name,wrong-import-position,import-error

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from pytest_mock import MockerFixture

EXAMPLES = Path(__file__).resolve().parents[3] / "examples" / "workflows"
sys.path.insert(0, str(EXAMPLES))

from contract_util import case_body, load, patch

from app.endpoints.workflows import start_workflow_handler
from models.config import WorkflowEngineConfiguration

# Highest #51 phase implemented in src/. Phase 0 is the Phase 0a hardening.
IMPLEMENTED_PHASE = 1

# workflow_engine keys of the example config whose phase is not built yet.
PENDING_ENGINE_KEYS = {"policy"}

CASES = load("cases.yaml")
STACK = load("lightspeed-stack.yaml")
DEFINITIONS = {
    name: load(f"{name}.yaml")
    for name in ("triage-github-issue", "kb-answer-with-approval", "admin-inline-mcp")
}


def _engine(stack: dict[str, Any]) -> WorkflowEngineConfiguration:
    """Build the real engine configuration from an example stack config."""
    engine = {
        k: v
        for k, v in stack["workflow_engine"].items()
        if k not in PENDING_ENGINE_KEYS
    }
    return WorkflowEngineConfiguration.model_validate(engine)


def _params(section: str) -> list[Any]:
    """Parametrize over a cases.yaml section, skipping later phases."""
    out = []
    for case in CASES[section]:
        marks = []
        if case["phase"] > IMPLEMENTED_PHASE:
            marks.append(pytest.mark.skip(reason=f"phase {case['phase']} not built"))
        out.append(pytest.param(case, id=case["name"], marks=marks))
    return out


def _expected(case: dict[str, Any]) -> int:
    """Expected status before cloud-agents#269."""
    exp = case["expect"]
    return exp["pre269"] if isinstance(exp, dict) else exp


@pytest.mark.parametrize("case", _params("workflow_cases"))
@pytest.mark.asyncio
async def test_workflow_case(case: dict[str, Any], mocker: MockerFixture) -> None:
    """The real POST /v1/workflows/run handler gives the contract's status."""
    import app.endpoints.workflows as wf_mod

    roles = set(case.get("as", CASES["defaults"][case["workflow"]]["as"]))
    cfg = mocker.patch("app.endpoints.workflows.configuration")
    cfg.workflow_engine_configuration = _engine(STACK)
    cfg.inference = SimpleNamespace(default_provider=None, default_model=None)
    cfg.spawner_configuration = (
        SimpleNamespace(sandbox_image=STACK["spawner"]["sandbox_image"])
        if case.get("spawner", True)
        else None
    )
    mocker.patch("app.endpoints.workflows.check_configuration_loaded")
    mocker.patch(
        "app.endpoints.workflows.is_admin", return_value="agent-admin" in roles
    )
    executor = mocker.AsyncMock()
    executor.start.return_value = "wf-contract"
    wf_mod._executor = executor  # pylint: disable=protected-access
    body = case_body(case, DEFINITIONS, CASES["defaults"])
    from models.api.requests.agents import RunWorkflowRequest

    try:
        request = RunWorkflowRequest(**body)
        await start_workflow_handler.__wrapped__(
            mocker.MagicMock(), request, ("uid", "user", False, "token")
        )
        status = 202
    except HTTPException as exc:
        status = exc.status_code
    finally:
        wf_mod._executor = None  # pylint: disable=protected-access

    assert status == _expected(case)
    assert executor.start.called == (status == 202)


@pytest.mark.parametrize("case", _params("config_cases"))
def test_config_case(case: dict[str, Any]) -> None:
    """The real WorkflowEngineConfiguration fails to load as the contract says."""
    stack = patch(STACK, case["set"])
    with pytest.raises(ValidationError) as exc_info:
        _engine(stack)
    assert case["error"].lower() in str(exc_info.value).lower()


def test_phase_tags_are_valid() -> None:
    """Every case is tagged with a known phase, so none is silently never run."""
    for section in ("workflow_cases", "config_cases"):
        for case in CASES[section]:
            assert case["phase"] in (0, 1, 2, 3), case["name"]


def test_example_config_loads_without_pending_blocks() -> None:
    """The example's implemented blocks load as a real WorkflowEngineConfiguration."""
    cfg = _engine(STACK)
    assert cfg.governed
    assert cfg.default_provider == "claude-prod"
