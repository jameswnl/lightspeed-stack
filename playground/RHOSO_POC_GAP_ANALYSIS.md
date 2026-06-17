# PoC Gap Analysis: pydantic-ai vs Goose for OpenStackAssistant CRD

**Context**: RHOSSTRAT-1276 defines an `OpenStackAssistant` CRD that deploys an AI
agent pod for cluster diagnostics. The current design uses Goose as the agent
runtime. This PoC evaluates replacing Goose with lightspeed-stack + pydantic-ai.

**Companion**: `lcore-pydantic-ai-diagnose-and-fix.md` — feasibility assessment
for autonomous diagnose-and-fix workflows using pydantic-ai.

## Capability Mapping

| Goose Capability | pydantic-ai Equivalent | Status | Notes |
|-----------------|----------------------|--------|-------|
| `goose session` interactive REPL | `agent.iter()` in a loop + `message_history` | **Works** | Demo 4 in `try_rhoso_upgrade.py`. pydantic-ai also ships a CLI REPL (`pydantic_ai._cli.run_chat()`). |
| `/cluster-health` recipe | `run_skill_script("cluster-health.py")` | **Works** | Skills framework supports executable scripts. See `examples/skills/rhoso-upgrade/scripts/cluster-health.py`. |
| `.goosehints` context file | Skills with progressive disclosure | **Better** | Skills loaded on-demand, not stuffed into context upfront. More token-efficient. |
| Direct `oc`/`openstack` shell execution | MCP tools (structured, typed) | **Better, with gap** | Safer (structured I/O, no injection, RBAC at tool level). Can't run arbitrary ad-hoc commands. See Gap 1. |
| Provider config (JSON Secret) | `LlamaStackProvider` | **Works** | Same Lightspeed Stack backend. |
| Recipe YAML files (slash commands) | Skills + scripts + resources | **Works differently** | Goose: slash-command triggered. pydantic-ai: LLM-discovered. See Gap 2. |
| Read-only RBAC (ServiceAccount) | MCP servers enforce read-only | **Better** | Defense in depth — RBAC + tool-level enforcement. |
| Container image with `oc`/`kubectl` | Lighter image (Python only) | **Better** | No Goose binary needed. MCP servers run in-process or as sidecars. |
| Diagnose → act → verify loop | `Agent.run()` with `output_validator` | **Better** | Agent autonomously chains tools, `output_validator` + `ModelRetry` enforces quality gates. See `try_rhoso_diagnose_and_fix.py`. |
| Human approval for actions | `ModelRetry` in tool | **Works, with gap** | Synchronous approval works. Async (web/Slack) needs state persistence. See Gap 6. |
| Free-text reports | Structured `UpgradeReadinessReport` (Pydantic model) | **Better** | Validated schema, machine-readable. Framework retries on schema mismatch. |

## Identified Gaps

### Gap 1: No Ad-Hoc Shell Command Execution
**Severity: Medium**

Goose can run arbitrary `oc` and `openstack` commands on the fly. pydantic-ai
relies on pre-defined MCP tools.

**Impact**: Admin debugging a novel issue needs `oc get pods -n openstack -l
app=nova-api -o wide` — a command no MCP tool covers.

**Mitigation options**:
1. **MCP tool for parameterized CLI execution** — add `run_oc_command(args: str)`
   and `run_openstack_command(args: str)` to the OpenStackClient MCP. Enforce
   read-only at the tool level (reject `delete`, `patch`, `edit`, `apply` verbs).
2. **Skills script execution** — `run_skill_script()` can run arbitrary scripts.
   A generic CLI wrapper script could accept commands as arguments.
3. **Accept the constraint** — MCP-only is safer. Admin can `oc exec` for ad-hoc
   commands; agent focuses on structured diagnostics.

**Recommendation**: Option 1. This MCP tool would be part of RHOSSTRAT-981
(OpenStackClient MCP, already In Progress).

### Gap 2: Slash Command / Recipe UX
**Severity: Low**

Goose: `/cluster-health` direct command. pydantic-ai: natural language
discovery via `list_skills` → `load_skill`.

**Mitigation**: Not really a gap — natural language is better UX. If explicit
commands are desired, the REPL can intercept `/`-prefixed input and map to
`run_skill_script()` calls.

### Gap 3: Pod Entrypoint / Session Management
**Severity: Medium**

CRD generates a Goose-specific entrypoint. Replacing Goose needs a new
entrypoint that starts a pydantic-ai session.

**What's needed**:
- Python entrypoint that initializes `LlamaStackProvider` from env vars
  (same provider Secret the CRD creates)
- Interactive REPL loop (PoC demo 4 is most of it)
- MCP server connections (in-process or via sidecar URLs)

**Effort**: Small. The CRD's `provider` field abstraction was designed for this.

### Gap 4: Session Persistence / Checkpointing
**Severity: Medium** (upgraded from Low based on diagnose-and-fix assessment)

pydantic-ai agents are ephemeral. `message_history` is in-memory. Two impacts:

1. **Disconnect/reconnect** — user loses conversation context
2. **Long-running workflows** (30+ min upgrade prep) — process crash loses
   all progress, no checkpoint/resume

**Mitigation**: Serialize `message_history` + `action_log` after each tool
round. lightspeed-stack already has conversation cache (`src/cache/`) and
the diagnose-and-fix PoC's `action_log` pattern.

**Comparison**: LangGraph has built-in checkpointing with configurable backends.
For lightspeed-stack, building a thin persistence layer on the existing cache
is lower-risk than adopting LangGraph.

**Related ticket**: LCORE-2281 (Durable agents / long-running tasks)

### Gap 5: Streaming Output
**Severity: Low**

The interactive REPL in demo 4 uses `agent.iter()` (waits for full response).
pydantic-ai supports streaming via `agent.run_stream()`.

**Mitigation**: Switch to `stream.stream_text(delta=True)` for the interactive
mode. Already demonstrated in `playground/try_pydantic_ai.py`.

### Gap 6: Async Human-in-the-Loop
**Severity: Medium** (new, from diagnose-and-fix assessment)

The PoC uses synchronous approval gates (auto-approved). In production,
destructive upgrade actions (migrate VMs, disable compute) may need async
approval (Slack, web UI, CLI prompt from a different session).

pydantic-ai has no native "pause and wait for human" mechanism. The tool
blocks the agent loop while waiting for approval. For async workflows:
- Need to break the agent loop, persist state, resume after approval
- `ModelRetry("User rejected. Try different approach.")` handles rejections

**Mitigation**: For synchronous (same-terminal) approval, the tool pattern
works today. For async approval, this requires the persistence layer from
Gap 4. Build persistence first, then async approval is straightforward.

**Comparison**: LangGraph has `interrupt_before` as a first-class concept.

### Gap 7: Context Window Management
**Severity: Low** (new, from diagnose-and-fix assessment)

A real diagnostic session generates thousands of tokens of tool results.
pydantic-ai has `history_processors` for trimming but no intelligent compaction.

**Mitigation**: lightspeed-stack already has conversation compaction
(`apply_compaction_blocking`). Adapt it for the agent loop.

## What pydantic-ai Does Better

1. **Structured tool calling** — MCP tools return typed data, not raw CLI output
   to parse. Eliminates parsing errors.

2. **Defense in depth** — RBAC at Kubernetes level AND tool level. Goose relies
   solely on RBAC.

3. **Quality gates** — `output_validator` + `ModelRetry` ensures the agent can't
   declare success while problems remain. The `try_rhoso_diagnose_and_fix.py`
   demo proves this: the validator checks actual cluster state against the
   agent's report. Goose has no equivalent.

4. **Self-correcting loops** — Agent autonomously discovers → diagnoses → fixes →
   verifies → retries. The framework manages the loop; no custom orchestration.
   Demonstrated end-to-end in `try_diagnose_and_fix.py` (12 tool calls, 2
   remediations, full verification).

5. **Progressive skill disclosure** — Skills loaded on-demand. Better token
   efficiency for large instruction sets.

6. **Multi-agent composition** — Specialist agents for compute, network, storage
   that the main agent delegates to (`try_multi_agent.py`).

7. **Structured output** — Pydantic-validated `UpgradeReadinessReport`, not free
   text. Enables downstream automation.

8. **Same backend** — `LlamaStackProvider` connects to the same Lightspeed Stack.
   No separate provider management.

9. **Lighter image** — Python + pydantic-ai, no Goose binary or oc/kubectl needed
   when using MCP.

## PoC Files

| File | What it demonstrates |
|------|---------------------|
| `playground/try_rhoso_upgrade.py` | Goose replacement: 4 demos (pre-upgrade, CLI gen, Q&A, interactive REPL) with 4 MCP servers + skills |
| `playground/try_rhoso_diagnose_and_fix.py` | Diagnose-and-fix loop: `output_validator` quality gate, approval gates, structured `UpgradeReadinessReport`, mutable state + verification |
| `playground/rhoso_mcp_server.py` | 4 FastMCP servers matching RHOSSTRAT child tickets (981, 980, 962, 979) |
| `examples/skills/rhoso-upgrade/` | Upgrade skill with SKILL.md, references, and `cluster-health.py` script |
| `playground/try_diagnose_and_fix.py` | Generic diagnose-and-fix pattern (companion to feasibility assessment) |

## Recommendation

pydantic-ai is a viable replacement for Goose. The quality gate (`output_validator`)
and structured output patterns are **strictly better** than what Goose provides —
the agent can't lie about cluster readiness because the validator checks actual state.

**Priority order to close gaps**:

1. **Add parameterized CLI tools to OpenStackClient MCP** (Gap 1) — RHOSSTRAT-981
   is already In Progress. Add `run_oc_command(args)` with verb filtering.

2. **Build pod entrypoint** (Gap 3) — Wrap the interactive REPL from demo 4.
   Update CRD controller to support `provider: "lightspeed"`.

3. **Build persistence layer** (Gap 4) — Serialize `message_history` + `action_log`
   using existing conversation cache. Enables resume-after-crash and async approval.

4. **Add streaming to REPL** (Gap 5) — Swap `agent.iter()` for `agent.run_stream()`.

5. **Async approval flow** (Gap 6) — Requires Gap 4 first. Build on persistence.

6. **Context compaction** (Gap 7) — Adapt existing `apply_compaction_blocking` for
   the agent loop. Only needed for very long sessions.
