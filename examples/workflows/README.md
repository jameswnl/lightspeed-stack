# Workflows on K8s: operator setup and caller guide (post-stack#51)

This folder is a self-contained, end-to-end worked example of the design in
[stack#51, revision 3](https://github.com/jameswnl/lightspeed-stack/issues/51#issuecomment-5911977694):
lightspeed-stack as the sole policy/secret boundary in front of cloud-agents.

> **Status: target state, being built (feature-level TDD).** This folder
> defines where issue #51 and cloud-agents#269 end up. Landed so far:
> Phase 0a (caller `credentials_secret` -> 400; custom images, `advisory`
> and `spawn: none/local` need admin -> 403; size and count caps) and
> Phase 1 (the provider catalog, secret registry and defaults load, and
> `/v1/workflows/run` resolves providers through them: the stack builds the
> credential reference, the same-entry rule holds, overrides are rewritten to
> executor types). Blocks marked `PLAN(stack#51)` that belong to later
> phases (`policy`, MCP `secret_headers` / `workflow_enabled`) do not load
> yet. `tests/unit/cloud_agents/test_workflow_contract.py` runs `cases.yaml`
> against the real handler and config for the implemented phases and skips
> the rest; `verify.py` runs all of it against the reference model.

> **Auth prerequisite.** Role-based policy and the admin gates need an auth
> module that resolves roles: `jwk-token` with `role_rules` (used here) or
> `rh-identity` with `access_rules`. With `k8s`, `noop`, `noop-with-token`
> or `api-key` the resolvers are no-ops: callers only have the `*` role,
> `access_rules` are ignored and every caller counts as admin, so the
> admin-only denials below do not apply.

## Contents

| File | Author | Purpose |
|---|---|---|
| `README.md` (this file) | — | Detailed steps: operator setup + authoring + triggering |
| `lightspeed-stack.yaml` | Operator | Provider catalog, secret registry, MCP catalog, policy, spawner |
| `k8s-secrets.yaml` | Operator | Secret values — the only place they exist |
| `triage-github-issue.yaml` | Caller | 2-step triage flow (role `agent-user`) |
| `kb-answer-with-approval.yaml` | Caller | Research → human approval → answer (role `agent-support`) |
| `admin-inline-mcp.yaml` | Caller (admin) | Inline MCP server with a registry secret ref (role `agent-admin`) |
| `post-269-overrides.yaml` | Operator | What changes after cloud-agents#269 (K8s-bound inference credentials, leases) |
| `k8s-deployment.yaml` | Operator | Hardened Deployment, least-privilege RBAC, NetworkPolicy |
| `external-secrets.yaml` | Operator | Production source of the Secrets (External Secrets Operator + Vault) |
| `cases.yaml` | Contract | 63 request/config cases with the expected status, per stage |
| `reference_gate.py` | Contract | Reference model of the gate; replaced by the real endpoint as phases land |
| `verify.py` | Contract | Runs everything above plus deployment cross-checks |

Assumed deployment: namespace `lightspeed`, stack Deployment + ConfigMap,
Postgres, OpenShell gateway for ephemeral sandboxes.

## Goal coverage (issue #51 acceptance criteria)

Each criterion maps to something in this folder that `verify.py` checks, so
"done" for a phase means more of these run against the real stack.

| #51 criterion | Where it shows up | Checked by |
|---|---|---|
| `ProviderSelection` / `SecretRef` contract | `provider: {name, model, credential_ref?}` in the requests below | `cases.yaml` (credential_ref match / mismatch) |
| Provider-profile and secret registry | `workflow_engine.providers` / `secrets` / `default_*` | load checks, both stages |
| Reject caller-chosen env var / physical ids | `credentials_secret` on run provider and `definition.provider`, unknown provider keys | `cases.yaml` § credentials (400) |
| AuthZ: provider, model, credential, MCP, skills, tools, spawn, images, service accounts, namespaces, limits | `workflow_engine.policy.rules` | `cases.yaml` § policy / spawn / limits (403) |
| In-process handoff contract | Part 3, "After cloud-agents#269" | `post-269-overrides.yaml` run through the same cases |
| Process-boundary handoff contract | same section (lease redeemed over mTLS; Temporal workers get `MCP_ALLOWED_SECRETS` from config) | design only; Phase 6 |
| No secret values in payloads, state, logs | requests carry logical names only; `k8s-secrets.yaml` has placeholders only | `verify.py` secrets checks; Phase 4 canary suite later |
| Inline MCP URLs / literal headers | `admin-inline-mcp.yaml`, `inline_mcp_hosts` | `cases.yaml` § inline MCP |
| OpenShell provider injection for ephemeral | `spawner` block, README Step 6 | config schema check |
| `none` / `local` restrictions | admin-only on workflows | `cases.yaml` § spawn |
| Rotation and revocation | Step 8 (pre-#269 rules, Reloader) and Part 3 (post-#269) | documented; lease tests in Phase 6 |
| Cleanup, redaction, cross-principal tests | not examples; Phase 4 / 6 test suites | cross-principal: `agent-support` vs triage, `agent-user` vs KB |
| Deployment docs: K8s secrets, external managers, OpenShell | `k8s-secrets.yaml` (dev), `external-secrets.yaml`, `k8s-deployment.yaml` | `verify.py` deployment cross-checks |
| Limits (`MAX_*`) | byte, step, MCP and secret-header caps | `cases.yaml` § limits (413 / 422) |

## The two documents (read this first)

Everything in this folder is one of two documents. They are authored by
different people, live in different places, and are joined by the stack at
submit time:

| | `lightspeed-stack.yaml` | Workflow definition YAML |
|---|---|---|
| Author | Operator (platform team) | Caller (API user / pipeline) |
| Lives in | ConfigMap mounted into stack pods | `POST /v1/workflows/run` request body |
| Contains | Catalogs, secret bindings, policy, spawner, MCP URLs | Steps, prompts, logical names, schemas |
| Secrets | Backend *bindings* only (env var / K8s Secret names), never values | Nothing secret-related — logical names only |

Core principle: workflow payloads may contain logical references to approved
configuration, but never secret material. The stack resolves every logical
name against its catalogs, checks policy for the caller's roles, and only
then persists and starts the run — otherwise 400 (unknown to the deployment)
or 403 (exists, but not for you) before anything is saved.

---

## Part 1 — Operator setup (once per deployment, K8s)

### Step 1. Put API keys and secrets in K8s Secrets

Keys live only in K8s Secrets. In production, do not apply values by hand:
`external-secrets.yaml` syncs every Secret below from Vault through External
Secrets Operator (`k8s-secrets.yaml` is the bootstrap/dev equivalent with
`CHANGEME` placeholders; `verify.py` checks both define the same Secrets and
keys). The Secrets:

- `lightspeed-inference-creds`: `ANTHROPIC_API_KEY`, `OPENAI_TEAM_B_KEY`
  (inference keys; pre-cloud-agents#269 bindings must be `backend: env`
  because cloud-agents reads `os.environ` by key).
- `mcp-github-readonly-token`: `token` (the full `Authorization` header
  value, including the `Bearer ` prefix; open question whether cloud-agents
  adds it, see cloud-agents#269).
- `mcp-kb-search-token`: `api-key` (KB service key).
- `mcp-incidents-token`: `token` (only reachable through admin inline MCP).
- `openshell-gateway-tls`: mTLS client identity for stack → gateway.
- `lightspeed-postgres`: `password` for the workflow-state database.
- `lightspeed-postgres-tls`: CA for `ssl_mode: verify-full`.

```bash
# Fill in every CHANGEME value first, then:
kubectl apply -n lightspeed -f k8s-secrets.yaml      # dev / bootstrap
kubectl apply -n lightspeed -f external-secrets.yaml  # production
```

`k8s-deployment.yaml` wires them into the stack Deployment (non-root,
read-only rootfs, no capabilities, digest-pinned image, default-deny egress
NetworkPolicy, RBAC limited to `get` on named Secrets). Excerpt — inference
keys and DB password as env, TLS as mounted files:

```yaml
# lightspeed-stack Deployment (excerpt)
env:
  - name: ANTHROPIC_API_KEY
    valueFrom: {secretKeyRef: {name: lightspeed-inference-creds, key: ANTHROPIC_API_KEY}}
  - name: OPENAI_TEAM_B_KEY
    valueFrom: {secretKeyRef: {name: lightspeed-inference-creds, key: OPENAI_TEAM_B_KEY}}
  - name: POSTGRES_PASSWORD
    valueFrom: {secretKeyRef: {name: lightspeed-postgres, key: password}}
volumeMounts:
  - {name: gw-tls, mountPath: /etc/openshell-tls, readOnly: true}
volumes:
  - name: gw-tls
    secret: {secretName: openshell-gateway-tls}
```

The stack's ServiceAccount also needs `get` on the MCP secrets (least
privilege: `resourceNames` listing exactly the K8s-bound Secrets; `verify.py`
checks the list matches the registry, and post-#269 adds the inference Secret).

### Step 2. Define the provider catalog, secret registry, and defaults

In `lightspeed-stack.yaml` under `workflow_engine` (`PLAN(stack#51)`):

- `providers`: one entry per logical provider. Each has exactly one
  `credential`, so choosing a credential means choosing an entry.
  `executor_type` must be in cloud-agents `APPROVED_INFERENCE_PROVIDERS`;
  `allowed_models: null` means any model (`[]` fails load).
- `secrets`: logical ref → backend binding (`env` / `k8s` / `file`).
- `default_provider` / `default_model`: the only defaults on cloud-agents
  paths. `inference.default_*` become Llama Stack-only.

This example ships two entries: `claude-prod` (anthropic,
`inference/anthropic-prod`, models pinned) and
`openai-team-b` (openai, `inference/openai-team-b`, any model).

Config load fails fast instead of misbehaving at runtime: unknown
`executor_type`, dangling ref, `allowed_models: []`, or bad defaults.

### Step 3. Register MCP servers with credentials as references

Each `mcp_servers[*]` entry carries the operator-owned URL plus two
`PLAN(stack#51)` fields:

- `workflow_enabled: true` — required before workflow runs can use it.
- `secret_headers: {Header: {name: <registry ref>}}` — header name to a
  registry ref, never a value.

This example: `github-readonly` (`Authorization` → `mcp/github-readonly`)
and `kb-search` (`X-API-Key` → `mcp/kb-search`).

Refused at load for `workflow_enabled` entries: request-bound
`authorization_headers` (`client`/`oauth`/`kubernetes`) and non-empty
propagated `headers` (e.g. `x-rh-identity`) — a detached run has no incoming
request, and silently dropping an identity header would run the server without
the caller's identity. The stack generates `MCP_ALLOWED_SECRETS` at startup
from the registry and refuses to start if the process env disagrees.

### Step 4. Provide skills via the sandbox image, govern with policy

Skills are directories baked into the sandbox image — the plan does not
change skill packaging:

```dockerfile
FROM quay.io/example/lightspeed-agentic-sandbox@sha256:a1b2c3d4a1b2c3d4a1b2c3d4a1b2c3d4a1b2c3d4a1b2c3d4a1b2c3d4a1b2c3d4
COPY ./skills/triage /skills/triage
COPY ./skills/kb-search /skills/kb-search
```

Enforcement differs by spawn mode: ephemeral gets a per-spawn Landlock
read-only grant on `/skills/<name>`; none/local get a
`SkillsCapability(include=[...])` allow-list. Default is least privilege:
omitted `allowed_skills` means no skills visible, not all. Which roles may
request which skill names is policy (next step). `advisory: true` (blanket
filesystem read in the sandbox) needs an explicit grant.

### Step 5. Write policy rules per role

`workflow_engine.policy.rules` (`PLAN(stack#51)`): each rule grants to a set
of roles; a principal's grant is the union of matching rules; nothing is
granted by default. Coarse RBAC (`WORKFLOW_START` etc. in `authorization`)
still gates "may submit at all"; policy gates "may use this".

This example's three roles:

- `agent-user` (triage flow): `claude-prod`, `github-readonly` +
  `mcp/github-readonly`, skill `triage`, tool `github.read`, spawn
  `ephemeral`, the pinned sandbox image, service account `workflow-runner`,
  namespace `sandbox-workloads`, limits (900s / 200k tokens / 2 retries).
- `agent-support` (KB flow): `openai-team-b`, `kb-search` +
  `mcp/kb-search`, skill `kb-search`, tool `kb.search`, spawn `ephemeral`,
  same image, service account `kb-runner`, same namespace, limits
  (1200s / 200k tokens / 1 retry).
- `agent-admin`: inherits both, plus both providers, `local`/`none` spawn,
  `inline_mcp` (+ allowed hosts, https-only, SSRF-checked), `advisory`.

### Step 6. Configure the spawner (OpenShell gateway on K8s)

```yaml
spawner:
  type: openshell
  openshell_gateway_url: "openshell-gateway.lightspeed.svc:443"
  openshell_workspace: lcore-prod
  sandbox_image: quay.io/example/lightspeed-agentic-sandbox@sha256:a1b2c3d4a1b2c3d4a1b2c3d4a1b2c3d4a1b2c3d4a1b2c3d4a1b2c3d4a1b2c3d4
  openshell_tls_ca: /etc/openshell-tls/ca.crt      # from openshell-gateway-tls
  openshell_tls_cert: /etc/openshell-tls/tls.crt
  openshell_tls_key: /etc/openshell-tls/tls.key
  max_pods: 50
```

The gateway owns the K8s-vs-Podman compute decision; the stack proxies
sandbox lifecycle through it. Ephemeral sandboxes receive credentials via
OpenShell provider injection — placeholder in the sandbox env, value only in
the supervisor proxy.

### Step 7. Deploy and verify the boundary

```bash
kubectl -n lightspeed create configmap lightspeed-config \
  --from-file=lightspeed-stack.yaml=./lightspeed-stack.yaml
kubectl apply -f k8s-deployment.yaml
kubectl -n lightspeed rollout status deploy/lightspeed-stack
kubectl -n lightspeed logs deploy/lightspeed-stack | grep -i "config\|provider\|MCP_ALLOWED"
```

Expected: clean start, generated `MCP_ALLOWED_SECRETS` (names only). Then
prove the denials from Part 2: caller-sent `credentials_secret` → 400,
unknown provider/MCP name → 400, valid-but-ungranted item → 403 with reasons,
`spawn: none` as `agent-user` on workflows → 403.

### Step 8. Rotation and revocation on K8s (pre-cloud-agents#269 rules)

- Update the value in Vault; External Secrets syncs the K8s Secret within
  `refreshInterval` and Reloader (annotation on the Deployment) **rolls the
  stack pods** so they pick up the new env values. By hand: `kubectl edit
  secret` + `kubectl rollout restart`. In-flight sandbox steps finish on the old value; new submissions
  use the new one after restart.
- Removing a ref from `secrets`/`policy` affects new submissions only —
  running steps are unaffected.
- After cloud-agents#269 (lease handoff), revocation fails closed at the
  next step (`credential_revoked`) and rotation applies at the next
  `acquire_value` with no restart.

---

## Part 2 — Caller: author and trigger a workflow (per run)

### Step 9. Author the `WorkflowDefinition`

Schema: `apiVersion` / `kind: AgentWorkflow` / `metadata` / `spec`, optional
top-level `provider` default and `skills` image. Per step:

- **Input**: `spec.input_prompt`, per-step `prompt` templates with
  `{{ steps.X.output.Y }}` references, per-step `context` dicts.
- **Output**: per-step `output_key` (state key) and optional `output_schema`
  (dict/JSON-schema constraining that step's output). No workflow-level
  output schema — results are read from state/transcripts.
- **Needs**: `mcp_servers` (catalog **names**), `allowed_skills`, `tools` /
  `permissions` (service account, `allowed_tools`, `max_tokens`),
  `target_namespaces`, `timeout_seconds`, `max_retries`, `spawn`.
- **Provider**: run-level `ProviderSelection {name, model, credential_ref?}`
  travels in the API request, not the definition. Optional
  `definition.provider` and per-step `inference_provider` must resolve to the
  **same catalog entry** as the run provider (same-entry rule; model may
  differ). Omit `credentials_secret` everywhere — it is rejected (400).
- **Never include**: secret values, raw `{secret_name, key}` headers, env var
  names, K8s Secret names, arbitrary images.

Merge rules the policy engine applies per step (same precedence as
cloud-agents' normalizer): `spawn` = step → workflow → `ephemeral`;
`mcp_servers` / `allowed_skills` / `permissions` / `service_account` = step →
workflow where `None` inherits and `[]` means explicitly empty;
`sandbox_image` = step `spawn_config` → workflow `spawn_config` → run value
→ spawner default; provider = step → `definition.provider` → run provider.

The workflow definitions in this folder (plus `admin-inline-mcp.yaml`, Part 3):

- `triage-github-issue.yaml`: triage → report; inherits run provider
  `claude-prod`; workflow-default MCP/skills with the `report` step opting
  out via `[]`.
- `kb-answer-with-approval.yaml`: research → human-approval `review` →
  respond; `definition.provider` = run entry `openai-team-b`; `research`
  overrides to model `gpt-4o-mini` on the same entry (allowed).

### Step 10. Trigger it: `POST /v1/workflows/run`

The definition travels inline in `definition`; the run-level provider is a
logical `ProviderSelection`. No secret material anywhere in the request.
The 8-step gate (authenticate → size → parse → catalog → policy → executor
validate → persist/start → audit) runs before anything is saved → `202`.

Triage flow (as `agent-user`):

```bash
DEFN=$(python3 -c "import json,yaml;print(json.dumps(yaml.safe_load(open('triage-github-issue.yaml'))))")
curl -s -X POST https://lightspeed.example.com/v1/workflows/run \
  -H "Authorization: Bearer $USER_TOKEN" -H 'Content-Type: application/json' \
  -d "$(jq -n --argjson defn "$DEFN" '{definition: $defn,
        provider: {name: "claude-prod", model: "claude-sonnet-4-5"}}')"
# -> 202 {"workflow_id": "<id>", "status": "running"}
```

KB flow (as `agent-support`):

```bash
DEFN=$(python3 -c "import json,yaml;print(json.dumps(yaml.safe_load(open('kb-answer-with-approval.yaml'))))")
curl -s -X POST https://lightspeed.example.com/v1/workflows/run \
  -H "Authorization: Bearer $SUPPORT_TOKEN" -H 'Content-Type: application/json' \
  -d "$(jq -n --argjson defn "$DEFN" '{definition: $defn,
        provider: {name: "openai-team-b", model: "gpt-4o"}}')"
# -> 202 {"workflow_id": "<id>", "status": "running"}
```

### Step 11. Follow the run; approve the gate

```bash
# Status / transcripts (both flows)
curl -s https://lightspeed.example.com/v1/workflows/<id> \
  -H "Authorization: Bearer $TOKEN"
curl -s https://lightspeed.example.com/v1/workflows/<id>/transcripts \
  -H "Authorization: Bearer $TOKEN"

# KB flow pauses at the `review` step; a role with workflow_approve continues it:
curl -s -X POST https://lightspeed.example.com/v1/workflows/<id>/approve \
  -H "Authorization: Bearer $SUPPORT_TOKEN" -H 'Content-Type: application/json' \
  -d '{"step_name": "review", "decision": "approved", "approver": "support-lead"}'

# Cancel a running workflow (role needs workflow_cancel):
curl -s -X POST https://lightspeed.example.com/v1/workflows/<id>/cancel \
  -H "Authorization: Bearer $TOKEN"
```

Denials to try: add `"credentials_secret"` to the request → 400; unknown
provider/MCP name → 400; `openai-team-b` as `agent-user` → 403; a step naming
a different catalog entry than the run provider → 400 (same-entry rule);
`spawn: none` as non-admin on workflows → 403.

---

## Part 3 — Other surfaces

> Chat on cloud-agents (`/query/direct`) is out of scope here; it was removed and is
> tracked in [#59](https://github.com/jameswnl/lightspeed-stack/issues/59).

### Admin inline MCP

`admin-inline-mcp.yaml` is the exception path: `https` only, no userinfo,
host in `inline_mcp_hosts`, no literal headers, and `secret_headers` as
registry refs (`{name: mcp/incidents}`) that the stack rewrites to
cloud-agents' form. The ref must also be granted via `mcp_secrets`. Prefer a
catalog entry; follow-up: named workflow templates so trusted pipelines do
not need `inline_mcp`.

### Audit events

Every decision leaves one structured, names-only event on the dedicated
`audit` logger (route it to its own sink). Examples of what operators should
see for the flows above:

```json
{"event": "workflow_authorized", "principal": "alice", "roles": ["agent-user"],
 "workflow_id": "wf-8f2c", "provider": "claude-prod", "model": "claude-sonnet-4-5",
 "credential_ref": "inference/anthropic-prod", "mcp_servers": ["github-readonly"],
 "spawn": {"triage": "ephemeral", "report": "ephemeral"}}
{"event": "workflow_denied", "principal": "alice", "status": 403,
 "reasons": ["provider 'openai-team-b' not granted"]}
{"event": "secret_accessed", "workflow_id": "wf-8f2c", "step": "triage",
 "purpose": "inference:anthropic", "ref": "inference/anthropic-prod",
 "backend": "k8s", "outcome": "granted"}
{"event": "lease_released", "workflow_id": "wf-8f2c", "step": "triage",
 "purpose": "inference:anthropic", "reason": "completed"}
```

No secret value appears in any event, log line, span attribute, metric label,
state record, transcript or API response (Phase 4 canary suite).

### After cloud-agents#269 (`post-269-overrides.yaml`)

`verify.py` runs every case against both stages, so you can see exactly what
changes:

| | Before cloud-agents#269 | After |
|---|---|---|
| Inference credential binding | `backend: env`; stack must be restarted to rotate | `backend: k8s`; read by the stack through the lease provider |
| Handoff | names (`env` key, K8s Secret name) | short-lived lease per step, redeemed then released; cross-process via mTLS |
| Revocation | new submissions only | next step fails closed (`credential_revoked`) |
| Rotation | restart | next lease, no restart |
| `secret_headers` MCP on `none`/`local` | 403 | allowed |
| Same-entry rule | enforced | can relax (each step has its own lease) |
| `spawn: none` / `local` | admin-only (process-wide env) | admin-only lifted once cloud-agents isolates them |
| Stack RBAC | `get` on MCP Secrets | `get` on MCP Secrets + the inference Secret |

## Gaps in the plan found while building these examples

Folded into the plan in revision 3 (phase in brackets):

1. **Inline MCP refs vs the typed parse.** cloud-agents' `MCPServerConfig`
   accepts `secret_headers` only as `{secret_name, key}` (`extra=forbid`), so a
   registry ref `{name: ...}` fails pipeline step 3 with 422 [Phase 3]. The stack must
   rewrite inline registry refs to the executor form for the shape check
   (modelled in `reference_gate._shape_copy`).
2. **`MCP_ALLOWED_SECRETS` must cover inline-granted secrets.** The plan
   generates it from `workflow_enabled` catalog servers only; a Secret reachable
   only through `rules[].mcp_secrets` (admin inline MCP) would be blocked by the
   runtime guardrail [Phase 3]. The generator here unions both.
3. **`ADMIN` is never in `authorized_actions`.** Admin gates use the access
   resolver with roles stored on `request.state.user_roles` (PR #57), and no-op
   auth modules (`k8s`, `noop`, `api-key`) make every caller admin [0a done;
   load check in Phase 2, decision D8].
4. **`Authorization` header values.** The K8s Secret holds the full header
   value (including `Bearer `); confirm cloud-agents does not add a prefix [Phase 3 round-trip test].

---

## Verification

`verify.py` is the executable contract. Run it from the repo root with the
project venv:

```bash
uv run python examples/workflows/verify.py
```

What it checks (~164 assertions):

- Every file parses; all three definitions pass the real cloud-agents
  `WorkflowDefinition` shape check.
- `lightspeed-stack.yaml` passes the plan's load-time checks in both stages
  (unknown `executor_type`, `allowed_models: []`, unbound refs, bad defaults,
  request-bound/propagated MCP headers, undefined
  policy names), and each of those failures is also pinned as a negative case.
- `cases.yaml`: accepted requests plus every denial (400 / 403 / 413 / 422)
  for credentials, catalog, policy, spawn, images, advisory, inline MCP,
  and limits, in `pre269` and `post269`.
- The non-`PLAN` subset validates against the current `Configuration` schema.
- The real `JwtRolesResolver` / `GenericAccessResolver` give the roles and
  admin semantics the policy assumes.
- Deployment cross-checks: env injection and `MCP_ALLOWED_SECRETS` match the
  registry, RBAC `resourceNames` match the K8s-bound Secrets, pod hardening,
  digest-pinned images, and the dev and External Secrets sources agree.

`reference_gate.py` is a model of the design, not the implementation. Each case
in `cases.yaml` carries a `phase`. The unit test
`tests/unit/cloud_agents/test_workflow_contract.py` runs the cases up to
`IMPLEMENTED_PHASE` against the real handler and `WorkflowEngineConfiguration`
(status only; `reason` strings belong to the reference) and skips later ones.
Finishing a phase means raising `IMPLEMENTED_PHASE`, dropping the matching
`PENDING_ENGINE_KEYS`, and deleting that part of the reference.

## Production checklist

- Images pinned by digest (stack, sandbox, policy `sandbox_images`).
- Values only in the secret manager; K8s Secrets synced, never committed.
- Role-resolving auth (`jwk-token` or `rh-identity`), not `k8s`/`noop`.
- `ssl_mode: verify-full` to Postgres; mTLS to the OpenShell gateway.
- Least-privilege RBAC by `resourceNames`; default-deny egress NetworkPolicy.
- Audit logger routed to a separate sink; alerts on `workflow_denied` spikes.
- Rotation runbook: pre-#269 restart via Reloader; post-#269 next lease.
- Deprecation window for the legacy `{name, model}` provider form
  (`Deprecation` / `Sunset` headers) communicated to callers before Phase 1.
