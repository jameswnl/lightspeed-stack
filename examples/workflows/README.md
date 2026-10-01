# Workflows on K8s: operator setup and caller guide (post-stack#51)

This folder is a self-contained, end-to-end worked example of the design in
[stack#51, revision 2](https://github.com/jameswnl/lightspeed-stack/issues/51#issuecomment-5911977694):
lightspeed-stack as the sole policy/secret boundary in front of cloud-agents.

> **Status: target state, being built (feature-level TDD).** This folder
> defines where issue #51 and cloud-agents#269 end up. Phase 0a is in
> review (PR #57): caller `credentials_secret` -> 400, custom images,
> `advisory` and `spawn: none/local` need admin -> 403, size and count
> caps. The `lightspeed-stack.yaml` blocks marked `PLAN(stack#51)` do not
> load yet, and the catalog/policy behavior in the workflows does not exist
> yet. `verify.py` pins the contract (see "Verification"); each phase should
> make more of it real until the whole folder runs against a deployment
> (Phases 0b–6).

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

Assumed deployment: namespace `lightspeed`, stack Deployment + ConfigMap,
Postgres, OpenShell gateway for ephemeral sandboxes.

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

Keys live only here. `k8s-secrets.yaml` in this folder defines all five:

- `lightspeed-inference-creds`: `ANTHROPIC_API_KEY`, `OPENAI_TEAM_B_KEY`
  (inference keys; pre-cloud-agents#269 bindings must be `backend: env`
  because cloud-agents reads `os.environ` by key).
- `mcp-github-readonly-token`: `token` (the full `Authorization` header
  value, including the `Bearer ` prefix; open question whether cloud-agents
  adds it, see cloud-agents#269).
- `mcp-kb-search-token`: `api-key` (KB service key).
- `openshell-gateway-tls`: mTLS client identity for stack → gateway.
- `lightspeed-postgres`: `password` for the workflow-state database.

```bash
# Fill in every CHANGEME value first, then:
kubectl apply -n lightspeed -f k8s-secrets.yaml
```

Wire them into the stack Deployment — inference keys and DB password as env,
TLS as mounted files:

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
privilege: `resourceNames: [mcp-github-readonly-token, mcp-kb-search-token]`).

### Step 2. Define the provider catalog, secret registry, and defaults

In `lightspeed-stack.yaml` under `workflow_engine` (`PLAN(stack#51)`):

- `providers`: one entry per logical provider. Each has exactly one
  `credential`, so choosing a credential means choosing an entry.
  `executor_type` must be in cloud-agents `APPROVED_INFERENCE_PROVIDERS`;
  `allowed_models: null` means any model (`[]` fails load);
  `direct_query_eligible: true` opts an entry into `/query/direct`.
- `secrets`: logical ref → backend binding (`env` / `k8s` / `file`).
- `default_provider` / `default_model`: the only defaults on cloud-agents
  paths. `inference.default_*` become Llama Stack-only.

This example ships two entries: `claude-prod` (anthropic,
`inference/anthropic-prod`, models pinned, `/query/direct`-eligible) and
`openai-team-b` (openai, `inference/openai-team-b`, any model).

Config load fails fast instead of misbehaving at runtime: unknown
`executor_type`, dangling ref, `allowed_models: []`, bad defaults, or an
eligible `/query/direct` entry bound to a non-default env key.

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
FROM quay.io/example/lightspeed-agentic-sandbox:1.4
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
  sandbox_image: quay.io/example/lightspeed-agentic-sandbox:1.4
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
kubectl -n lightspeed rollout restart deploy/lightspeed-stack
kubectl -n lightspeed logs deploy/lightspeed-stack | grep -i "config\|provider\|MCP_ALLOWED"
```

Expected: clean start, generated `MCP_ALLOWED_SECRETS` (names only). Then
prove the denials from Part 2: caller-sent `credentials_secret` → 400,
unknown provider/MCP name → 400, valid-but-ungranted item → 403 with reasons,
`spawn: none` as `agent-user` on workflows → 403.

### Step 8. Rotation and revocation on K8s (pre-cloud-agents#269 rules)

- `kubectl edit secret` + **restart the stack pods** to pick up new env
  values. In-flight sandbox steps finish on the old value; new submissions
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

The two definitions in this folder:

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

## Verification

Verified by `verify.py` in this folder — run from the repo root with the
project venv (it resolves its own paths, so it works from anywhere):

```bash
.venv/bin/python examples/workflows/verify.py
```

What it checks:

- All YAML files parse; both definitions validate under the real
  cloud-agents `WorkflowDefinition` model.
- The auth block builds the real `JwtRolesResolver` and
  `GenericAccessResolver`: each role maps from a token, only `agent-admin`
  is admin, and `agent-support` alone may approve.
- 55-assertion replay of the plan's submission gate (steps 4–5): every
  provider/model/MCP/secret/skill/tool/spawn/image/service-account/
  namespace/limit referenced by both definitions resolves in the catalogs
  and is granted to the intended role.
- The non-`PLAN` subset of `lightspeed-stack.yaml` validates against the
  current `Configuration` schema.
