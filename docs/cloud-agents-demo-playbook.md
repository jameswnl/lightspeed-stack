# Cloud-agents demo playbook: direct, ephemeral, and workflow, with tracing + transcripts

A live-demo script for showing the team `spawn: none` (direct) and `spawn:
ephemeral` (sandboxed) agent execution, a multi-step workflow with human
approval, OpenTelemetry spans in Jaeger, and transcript retrieval.

Companion to `docs/cloud-agents-demo-curl.sh`, which does the actual HTTP
calls this playbook walks through. For bringing the environment up from
scratch (Kind cluster, OpenShell gateway, mock MCP server, Jaeger,
Postgres), see `docs/cloud-agents-demo-environment.md` first.

## 0. Prerequisites checklist

- `OPENAI_API_KEY` exported
- OpenShell gateway reachable at `localhost:9080` (local Kind cluster via
  infra4dev — `make kind-openshell-up`)
- Mock pod-status MCP server deployed in the cluster and port-forwarded to
  `localhost:8084` (needed by the `agent-none`/`agent-local` scenarios)
- Jaeger UI reachable at `http://localhost:16686`, OTLP HTTP receiver at
  `http://localhost:4318` (confirm: `curl -s localhost:16686/api/services`)
- Postgres reachable on `localhost:5432` with user/password/database
  `lightspeed` (LCS's workflow-storage lifespan needs it even for
  single-agent runs): `nc -z localhost 5432`

## 1. Point the harness config at the gateway

```bash
export OPENSHELL_GATEWAY_URL=localhost:9080
# The local Kind gateway runs with TLS disabled, so the spawner needs an
# explicit opt-in before it will send the LLM credential over cleartext
# gRPC. Without this, every spawn:ephemeral request 500s with
# "Provider creation requires TLS (OPENSHELL_TLS_CA)".
export OPENSHELL_ALLOW_INSECURE_CREDENTIALS=1
```

If you hit TLS/auth errors against a different gateway, add to
`lightspeed-stack-harness.yaml`'s `spawner:` block:

```yaml
spawner:
  type: openshell
  openshell_gateway_url: "${env.OPENSHELL_GATEWAY_URL:=localhost:9080}"
  sandbox_image: "${env.LIGHTSPEED_SANDBOX_IMAGE:=quay.io/jameswong/lightspeed-agentic-sandbox:latest}"
  openshell_tls_ca: /path/to/ca.pem                 # only if TLS handshake fails
  openshell_bearer_token: "${env.OPENSHELL_TOKEN}"  # only if auth required
```

## 2. Start LCS with tracing on, pointed at your running Jaeger

```bash
export OTEL_SDK_DISABLED=false
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
export OTEL_SERVICE_NAME=lightspeed-core-demo

uv run make run-stack CONFIG=lightspeed-stack-harness.yaml
```

You'll see `Failed to connect to Llama Stack... Entering degraded mode` at
startup -- **expected, harmless**: cloud-agents talks to OpenAI directly,
never touches Llama Stack. Wait for `Uvicorn running on http://0.0.0.0:8090`.

Note: spans are exported under the service name **`workflow-runner`**, not
`OTEL_SERVICE_NAME` -- cloud-agents' `init_tracing("workflow-runner")`
hardcodes it. Select `workflow-runner` in Jaeger.

## 3. Direct mode (spawn: none) -- in-process

```bash
BASE_URL=http://localhost:8090 ./docs/cloud-agents-demo-curl.sh agent-none
```

Narrate: `"spawn": "none"` runs in-process, no sandbox. The response's
`transcript` array is the full LLM exchange, printed right there.

## 4. Ephemeral mode (spawn: ephemeral) -- real sandbox via the gateway

```bash
BASE_URL=http://localhost:8090 ./docs/cloud-agents-demo-curl.sh agent-ephemeral
```

Narrate: same endpoint, `"spawn": "ephemeral"` -- LCS calls the OpenShell
gateway, spins up a real sandbox pod, runs the agent inside it, tears it
down. Same response shape, same inline `transcript`. This scenario also
demonstrates skills + Landlock: only the `k8s-diag` skill is materialized
into the sandbox, and the agent's attempt to read the unlisted
`/skills/security-audit/SKILL.md` is denied at the filesystem boundary --
listen for the agent reporting the read as denied.

First ephemeral call after a cluster rebuild pulls the ~500 MB sandbox
image and can take a minute; subsequent calls run in ~10-20 s.

## 5. Workflow run -- multi-step, with human approval

The richer orchestration demo: a 3-step workflow (`triage` -> `approve` ->
`remediate`), both agent steps running `spawn: ephemeral`, pausing
mid-flight for a human decision.

```bash
BASE_URL=http://localhost:8090 ./docs/cloud-agents-demo-curl.sh workflow-ephemeral-approval
```

Narrate as it runs -- the script itself walks through each phase out loud
via its own echoed section headers:

1. `POST /v1/workflows/run` -- kicks off the workflow, returns a
   `workflow_id` immediately (202 Accepted; async execution starts in the
   background).
2. The script polls `GET /v1/workflows/{id}` until status is `paused` --
   the `triage` step (ephemeral sandbox) has run and produced a
   `severity`/`root_cause`, and the workflow is now blocked at the
   `approve` step waiting on a human.
3. `POST /v1/workflows/{id}/approve` -- the human-in-the-loop moment. In a
   real deployment this would be a person reviewing the triage output
   before authorizing remediation.
4. The script polls again until `completed` -- the `remediate` step
   (another ephemeral sandbox) runs, referencing
   `{{ steps.triage_result.output.root_cause }}` from the earlier step's
   output (step-chaining).
5. Finally it hits `GET /v1/workflows/{id}/transcripts` and prints the
   full per-step transcript.

Keep the `workflow_id` printed at the top -- you'll want it in step 7.

Step-chaining note: the `remediate` prompt's `{{ steps.triage_result... }}`
template is interpolated before the sandbox request ships (fixed by
[lightspeed-core#274](https://github.com/lightspeed-core/lightspeed-cloud-agents/issues/274);
the `triage` step in this demo payload carries no MCP servers, so its
`root_cause` is the model's best tool-less diagnosis -- if you want the
OOMKilled story, narrate it from `agent-ephemeral`'s tool-backed run).

## 6. Show the Jaeger spans

Open `http://localhost:16686`, select service `workflow-runner`, click
**Find Traces**. You should see traces for all the calls above:

- **Direct-mode trace**: HTTP span for `POST /v1/agents/run` -> nested
  `llm.call` span.
- **Ephemeral agent-run trace**: same, plus sandbox lifecycle spans
  (create/exec/destroy against the gateway) nested underneath.
- **Workflow trace**: a `workflow.execute` root span containing three
  child `step.execute` spans (`triage`, `approve`, `remediate`) -- the
  `approve` step's span will show a visibly longer duration if there was
  any delay between the pause and your `approve` call, since the span
  covers the full wait. The two agent steps each nest their own sandbox
  lifecycle spans, same as step 4's trace.

Good talking points: span duration side-by-side (ephemeral vs. direct
latency cost), and the workflow trace's step-nesting showing exactly
where time went across the whole orchestration.

## 7. Show the transcript -- inline vs. DB-persisted

- **Agent-run calls (steps 3-4)**: transcript is inline in the HTTP
  response only -- good for a single call, not queryable later.
- **Workflow call (step 5)**: transcript is persisted to Postgres and
  independently queryable:

  ```bash
  curl -s http://localhost:8090/v1/workflows/<workflow_id>/transcripts | jq
  ```

  Each step's entry includes a `trace_id` column. Copy it and paste into
  Jaeger's trace-ID search (`http://localhost:16686/trace/<trace_id>`) to
  jump straight from a stored, queryable transcript record to its exact
  live span tree -- the strongest "traceability" moment of the whole
  demo: **DB record -> trace_id -> span tree**, for a workflow that
  already finished and whose process may not even be running anymore.

---

**Live demo order**: steps 1-2 beforehand (setup), then 3 -> 4 -> 5 -> 6
-> 7 live. Step 5 takes the longest (two sandbox boots + a pause for your
manual approval call) -- budget ~1-2 minutes for it, and don't rush the
approve step, it's the payoff moment.

Do a full dry run against your actual gateway before presenting to the
team -- TLS/auth requirements vary by gateway and are worth finding out
in advance, not live.
