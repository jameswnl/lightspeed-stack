# Cloud-agents e2e testing strategy

## Problem

An audit of `tests/e2e/cloud_agents/` found real coverage gaps across the
two cloud-agents HTTP endpoints, `/v1/agents/run` and `/v1/workflows/*`,
crossed with the three spawn modes:

| Endpoint | none | local | ephemeral |
|---|---|---|---|
| `/v1/agents/run` | HTTP-tested | not a valid API value (`AgentRunRequest.spawn: Literal["none","ephemeral"]`) | only handler-direct, bypassing HTTP routing/auth |
| `/v1/workflows/run` | HTTP-tested | zero coverage | zero coverage |

None of `tests/e2e/cloud_agents/` ran in CI (`.github/workflows/cloud_agents_tests.yaml`
only ran `tests/unit/` and `tests/integration/`), and every e2e test required
a real `OPENAI_API_KEY` against real OpenAI.

## Goals

1. Full e2e coverage across the whole (endpoint × spawn mode) matrix,
   runnable locally through the real HTTP endpoints with a real
   `OPENAI_API_KEY` and, for ephemeral, a real `OPENSHELL_GATEWAY_URL`.
2. CI coverage for spawn=none/local (no external gateway needed), without
   requiring real OpenAI egress in CI.

## Design

### `RunWorkflowRequest` already supports local/ephemeral steps

`RunWorkflowRequest` has no top-level `spawn` field — spawn is per-step,
inside `definition.spec.steps[].spawn`, read directly by the step
dispatcher (`cloud_agents.workflow.executor.step.dispatch.get_step_executor`).
So `/v1/workflows/run` already supported `local`/`ephemeral` steps over
real HTTP before this change — the gap was purely a missing test, not
missing functionality. `test_workflows_http_e2e.py` gained
`test_workflow_with_local_spawn_step` and
`test_workflow_with_ephemeral_spawn_step` accordingly, and
`test_agents_run_http_e2e.py::TestAgentRunHttpE2E` gained
`test_ephemeral_agent_run` to cover the last real gap (agents×ephemeral
had only handler-direct coverage before).

(These two files started as one combined `test_agents_workflow_http_e2e.py`
and were later split by endpoint as part of a broader
`tests/e2e/cloud_agents/` reorganization by testing layer.)

### Mock LLM server for CI

`spawn=local` runs the LLM call in a **child subprocess**
(`SubprocessExecutor`, which copies `os.environ` at fork time). This rules
out in-process mocking (`respx`/`mocker.patch`) — the only thing that
works uniformly for `none` and `local` is a real listening HTTP endpoint
reached via `OPENAI_BASE_URL`, which the `openai` SDK already reads from
env with zero cloud-agents code changes.

cloud-agents' `to_model_string()` always produces a bare `"openai:<model>"`
string, which pydantic-ai routes to `OpenAIResponsesModel` → `POST
/v1/responses`, always non-streaming for the plain-prompt path these
tests use (no `Agent`/tools wired for a bare agent-run). So the mock
(`tests/e2e/cloud_agents/mock_llm_server.py`) only implements that one
endpoint, no streaming, no tool-call output items — a
`ThreadingHTTPServer` returning a canned `openai.types.responses.Response`-shaped
body.

One subtlety discovered while wiring this up: cloud-agents' **native
structured-output mode** (used whenever a step sets `output_schema`) sends
`text.format.schema` in the request and then `json.loads()`s the returned
text directly — a plain-prose canned response fails with "LLM returned
non-JSON response but output_schema was requested". The mock detects a
`json_schema`-format request and returns a JSON string satisfying the
schema's required properties instead
(`_placeholder_for_schema`/`_response_text_for_request` in
`mock_llm_server.py`), verified against the OpenAI SDK's real response
schema and against a real `pydantic_ai.direct.model_request()` call before
being wired into the test suite.

`tests/e2e/cloud_agents/conftest.py` gates the redirect behind a new
`LIGHTSPEED_E2E_USE_MOCK_LLM` env var (logic lives in `mock_llm_env.py`,
unit-tested directly): unset (the default), tests hit real OpenAI exactly
as before; set (what CI does), `OPENAI_BASE_URL` is redirected to the
mock for the session.

**Scope warning:** this only applies safely to
`test_agents_run_http_e2e.py`/`test_workflows_http_e2e.py` (plus the mock's own self-tests) —
every other file in `tests/e2e/cloud_agents/` asserts real-world semantic
LLM content (e.g. `"paris" in output`) that only a real model can produce,
and fails confusingly (not due to a real bug) if run with the mock
active. The CI job and the `test-e2e-agents-workflows-mock` Makefile
target both scope to the compatible files explicitly rather than the
whole directory.

### Ephemeral tests are backend-agnostic by design

Client-side ephemeral tests don't need separate Kind-specific vs.
OCP-specific code — `OpenShellSpawner`'s entire lifecycle is
gateway-mediated over gRPC, and the gateway itself decides its own compute
driver (kubernetes vs. podman). One test, pointed at whatever
`OPENSHELL_GATEWAY_URL` is reachable, exercises either backend.

### `ephemeral` pytest marker

Registered in `pyproject.toml` and applied to every ephemeral-gated test
(existing and new), so CI can cleanly exclude them with
`-m "not ephemeral"`. The runtime gateway-health skip
(`SandboxClient(...).health()`) remains the actual safety net regardless
— the marker is for fast, clean exclusion, not the only guard.

### Harness config gained a `spawner` section

`lightspeed-stack-harness.yaml` had no `spawner:` section at all, which
meant `spawn=ephemeral` would 400 against a server started with it —
including `docs/cloud-agents-demo-curl.sh`'s `agent-ephemeral` scenario,
which already documented an ephemeral flow that couldn't have worked. Added:

```yaml
spawner:
  type: openshell
  openshell_gateway_url: "${env.OPENSHELL_GATEWAY_URL:=localhost:17670}"
  sandbox_image: "${env.LIGHTSPEED_SANDBOX_IMAGE:=quay.io/jameswong/lightspeed-agentic-sandbox:latest}"
```

using the same `${env.VAR:=default}` substitution already used elsewhere
in this config, matching the env var names/defaults the existing ephemeral
test files already used independently.

## CI wiring

New `e2e-mock` job in `.github/workflows/cloud_agents_tests.yaml`, running
automatically on every push/PR to `harness` (same trigger as the existing
unit/integration job): a `postgres:16` service matching the harness
config's credentials, `OPENAI_API_KEY=sk-mock-ci-key` +
`LIGHTSPEED_E2E_USE_MOCK_LLM=1`, running
`test_agents_run_http_e2e.py` and `test_workflows_http_e2e.py` plus the mock's own self-tests with
`-m "not ephemeral"`.

## File organization by testing layer

`tests/e2e/cloud_agents/` is organized by which layer of the stack a test
actually exercises, not just by topic -- an earlier version of this suite
used an undifferentiated `*_e2e.py` suffix for tests spanning five
different layers, which produced misleading names (e.g. a `TestQueryDirectE2E`
class that never touched `/v1/query/direct`) and duplicated coverage
(the same workflow YAML executed near-identically under the same class
name in two different files). Current layout:

| File | Layer | Covers |
|---|---|---|
| `test_agents_run_http_e2e.py` | real HTTP (`TestClient`) | `/v1/agents/run`, spawn none+ephemeral |
| `test_workflows_http_e2e.py` | real HTTP (`TestClient`) | `/v1/workflows/*`, spawn none+local+ephemeral |
| `test_agents_run_handler_e2e.py` | handler-direct (`handler.__wrapped__(...)`) | `/v1/agents/run`, spawn none+ephemeral |
| `test_query_direct_handler_e2e.py` | handler-direct | `/v1/query/direct` error paths |
| `test_step_executor_e2e.py` | step-executor dispatch (`get_step_executor(...).run(...)`) | single-step execution, spawn none+local+ephemeral |
| `test_workflow_definitions_e2e.py` | step-executor dispatch | full workflow-YAML execution, one step-executor call per step |
| `test_otel_tracing_e2e.py` | mid-layer (`execute_query_via_direct_executor`) | trace/span assertions for the query/direct path |
| `test_workflow_tracing_e2e.py` | `LocalWorkflowRunner` directly | trace/span assertions for the workflow-engine path |
| `mock_llm_server.py`, `mock_llm_env.py`, `test_mock_llm_*.py`, `jaeger_helpers.py`, `conftest.py` | infra | shared fixtures/mocks, not endpoint tests themselves |

Rule of thumb when adding a new e2e test here: pick the file whose layer
matches how you're actually invoking the code (real HTTP vs. handler vs.
step-executor), not the file whose name sounds topically closest -- that's
exactly the mismatch that caused the confusion this reorg fixed.

## Running the full matrix locally

```bash
# none/local, real OpenAI:
OPENAI_API_KEY=... uv run make test-e2e-agents-workflows

# none/local, no credentials needed (mock LLM):
uv run make test-e2e-agents-workflows-mock

# full matrix including ephemeral, real OpenAI + a reachable gateway
# (Kind-deployed or real OCP -- either works, same test code):
OPENAI_API_KEY=... OPENSHELL_GATEWAY_URL=<host:port> uv run make test-e2e-agents-workflows
```
