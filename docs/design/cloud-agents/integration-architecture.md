# Cloud Agents Integration Architecture

## Overview

Lightspeed-stack integrates cloud-agents as a pip dependency, gaining multi-step workflow orchestration, multi-spawn-mode agent execution, and a unified execution engine that will eventually replace the Llama Stack agent path.

```mermaid
graph TB
    subgraph ls["lightspeed-stack"]
        subgraph existing["Existing Endpoints"]
            query["/query"]
            stream["/streaming_query"]
            responses["/responses"]
            a2a["/a2a"]
        end
        subgraph new["New Endpoints"]
            qd["/query/direct"]
            qds["/query/direct/stream"]
            ar["/agents/run"]
            wf["/workflows/*"]
            at["/agent-tools"]
        end

        existing -->|build_agent| llamastack["Llama Stack"]
        new --> bridge["query_executor.py\nvalidate + build StepInput"]
    end

    bridge --> dispatch["get_step_executor()"]

    subgraph ca["cloud-agents (pip dep)"]
        dispatch --> none["DirectExecutor\nspawn: none"]
        dispatch --> local["SubprocessExecutor\nspawn: local"]
        dispatch --> ephemeral["SandboxExecutor\nspawn: ephemeral"]
    end

    none -->|in-process| pydantic["pydantic-ai Agent"]
    local -->|child process| pydantic2["pydantic-ai Agent"]
    ephemeral -->|container| sandbox["Sandbox Container\n(any SDK)"]

    pydantic --> llm["LLM Provider\n(OpenAI, etc.)"]
    pydantic2 --> llm
    sandbox --> llm

    style existing fill:#f0f0f0,stroke:#999
    style new fill:#e8f5e9,stroke:#4caf50
    style ca fill:#e3f2fd,stroke:#2196f3
    style llamastack fill:#fff3e0,stroke:#ff9800
```

## Spawn Modes

The spawn mode determines where and how an agent executes. The same agent definition runs in any mode -- the mode is an isolation choice, not a capability choice.

### spawn: none (DirectExecutor)

```mermaid
sequenceDiagram
    participant Client
    participant FastAPI
    participant DirectExecutor
    participant Agent as pydantic-ai Agent
    participant LLM as OpenAI API
    participant MCP as MCP Server

    Client->>FastAPI: POST /agents/run
    FastAPI->>DirectExecutor: run(StepInput)
    DirectExecutor->>Agent: Agent("openai:gpt-4o-mini")
    Agent->>LLM: inference call
    LLM-->>Agent: response
    opt MCP tools configured
        Agent->>MCP: tool call via MCPToolset
        MCP-->>Agent: tool result
        Agent->>LLM: follow-up inference
        LLM-->>Agent: final response
    end
    Agent-->>DirectExecutor: AgentRunResult
    DirectExecutor-->>FastAPI: StepResult
    FastAPI-->>Client: JSON response
```

**When to use:** Default for all queries and workflow steps. Low latency, no infrastructure needed.

**Capabilities:** LLM, MCP tools, registered @step_tool functions, skills, streaming.

**Isolation:** None. Agent runs in the same process as the API server.

### spawn: local (SubprocessExecutor)

```mermaid
sequenceDiagram
    participant Client
    participant FastAPI
    participant SubprocessExec as SubprocessExecutor
    participant Child as Child Process
    participant Agent as pydantic-ai Agent
    participant LLM as OpenAI API

    Client->>FastAPI: POST /agents/run
    FastAPI->>SubprocessExec: run(StepInput)
    SubprocessExec->>Child: spawn subprocess
    Note over Child: Inherits env vars<br/>(OPENAI_API_KEY, etc.)
    Child->>Agent: create Agent with tools
    Agent->>LLM: inference call
    LLM-->>Agent: response
    Agent-->>Child: result
    Child-->>SubprocessExec: JSON via stdout
    Note over Child: Process exits
    SubprocessExec-->>FastAPI: StepResult
    FastAPI-->>Client: JSON response
```

**When to use:** Steps with untrusted tools, crash isolation needed, or resource-intensive operations.

**Capabilities:** Same as spawn:none (LLM, MCP, tools, skills). MCP connections opened in child process.

**Isolation:** Process boundary. Child crash doesn't affect server.

### spawn: ephemeral (SandboxExecutor)

```mermaid
sequenceDiagram
    participant Client
    participant FastAPI
    participant SandboxExec as SandboxExecutor
    participant Spawner as OpenShellSpawner
    participant Gateway as OpenShell Gateway
    participant Container as Sandbox Container
    participant LLM as OpenAI API

    Client->>FastAPI: POST /agents/run
    FastAPI->>SandboxExec: run(StepInput)
    SandboxExec->>Spawner: spawn(image, env, labels)
    Spawner->>Gateway: CreateSandbox
    Note over Gateway: Gateway's own compute driver<br/>(kubernetes or podman) decides<br/>how the sandbox is created --<br/>not something the client sends
    Gateway->>Container: create sandbox
    Spawner->>Gateway: ExposeService
    Note over Gateway,Container: All further HTTP calls are<br/>proxied through the gateway's own<br/>address (Host-header routed) --<br/>never a direct pod/container IP
    SandboxExec->>Gateway: wait_ready(/health)
    Gateway->>Container: /health
    SandboxExec->>Gateway: POST /v1/agent/run
    Gateway->>Container: POST /v1/agent/run
    Note over Container: Any SDK inside<br/>(Claude Code, OpenAI Agents,<br/>pydantic-ai, etc.)
    Container->>LLM: inference call
    LLM-->>Container: response
    Container-->>Gateway: JSON result
    Gateway-->>SandboxExec: JSON result
    SandboxExec->>Gateway: GET /v1/agent/events
    Gateway->>Container: GET /v1/agent/events
    Container-->>Gateway: transcript
    Gateway-->>SandboxExec: transcript
    SandboxExec->>Spawner: destroy(sandbox_name)
    Spawner->>Gateway: DeleteSandbox
    SandboxExec-->>FastAPI: StepResult
    FastAPI-->>Client: JSON response
```

**When to use:** Full isolation needed, different agent SDK required, or agents with kubectl/filesystem access.

**Capabilities:** Determined by the container image. The sandbox is a black box.

**Isolation:** Container boundary. Network policy, filesystem, and resource limits enforced by K8s/Podman.

## Workflow Execution

Multi-step workflows are orchestrated by `LocalWorkflowRunner` (pydantic-graph) or `TemporalWorkflowRunner` (Temporal, for crash recovery).

```mermaid
graph TD
    start["POST /v1/workflows/run"] --> runner["LocalWorkflowRunner"]

    runner --> step1["Step 1: triage\nspawn: none"]
    step1 -->|DirectExecutor| llm1["LLM call"]
    llm1 --> result1["output: {severity, category}"]

    result1 --> step2["Step 2: approve\ntype: human-approval"]
    step2 -->|PAUSE| wait["Await POST /approve"]
    wait -->|approved| step3

    result1 -.->|"{{ steps.triage.output.summary }}"| step3["Step 3: remediate\nspawn: ephemeral"]
    step3 -->|SandboxExecutor| container["Container execution"]
    container --> result3["output: {fix_plan}"]

    result3 --> done["Workflow completed"]

    subgraph state["PostgreSQL"]
        rss["RunStateStore\n(workflow state)"]
        ts["TranscriptStore\n(step transcripts)"]
    end

    runner -.-> rss
    llm1 -.-> ts
    container -.-> ts

    style step1 fill:#e8f5e9,stroke:#4caf50
    style step2 fill:#fff3e0,stroke:#ff9800
    style step3 fill:#e3f2fd,stroke:#2196f3
    style state fill:#f5f5f5,stroke:#999
```

### Step Output Chaining

Each step's output is available to subsequent steps via template interpolation:

```yaml
steps:
  - name: analyze
    prompt: "Analyze this alert: ..."
    output_key: analysis

  - name: fix
    prompt: >
      Based on the analysis:
      {{ steps.analysis.output.summary }}
      Generate a fix plan.
```

## Architecture Layers

```mermaid
graph TB
    subgraph http["HTTP Layer (lightspeed-stack)"]
        endpoints["FastAPI endpoints, auth, RBAC, SSE streaming"]
    end

    subgraph bridge["Bridge Layer (lightspeed-stack src/workflow/)"]
        qe["query_executor.py"]
        storage["storage.py"]
        ef["executor_factory.py"]
    end

    subgraph exec["Execution Layer (cloud-agents)"]
        se["StepExecutor ABC"]
        wr["WorkflowRunner ABC"]
        de["DirectExecutor"]
        sube["SubprocessExecutor"]
        sande["SandboxExecutor"]
        lwr["LocalWorkflowRunner"]
    end

    subgraph agent["Agent Layer (pydantic-ai)"]
        pai["pydantic-ai Agent"]
        mcp["MCPToolset"]
        tools["@step_tool"]
    end

    subgraph provider["Provider Layer"]
        openai["OpenAI"]
        anthropic["Anthropic"]
        azure["Azure"]
    end

    http --> bridge
    bridge --> exec
    exec --> agent
    agent --> provider

    style http fill:#e8f5e9
    style bridge fill:#fff3e0
    style exec fill:#e3f2fd
    style agent fill:#f3e5f5
    style provider fill:#f5f5f5
```

## API Surface

| Endpoint | Method | Purpose |
|---|---|---|
| `/v1/query/direct` | POST | Blocking query via DirectExecutor |
| `/v1/query/direct/stream` | POST | SSE streaming query |
| `/v1/agents/run` | POST | Single agent execution (any spawn mode) |
| `/v1/workflows/run` | POST | Start a multi-step workflow |
| `/v1/workflows/{id}` | GET | Get workflow status |
| `/v1/workflows/{id}/approve` | POST | Approve a paused step |
| `/v1/workflows/{id}/cancel` | POST | Cancel a workflow |
| `/v1/workflows/{id}/transcripts` | GET | Get step transcripts |
| `/v1/agent-tools` | GET | List registered tools |

## Configuration

```yaml
# lightspeed-stack.yaml

workflow_engine:
  enabled: true
  max_concurrent_workflows: 10
  transcript_retention_days: 30

spawner:
  type: openshell
  sandbox_image: lightspeed-agentic-sandbox:latest
  max_pods: 10
  openshell_gateway_url: openshell-gateway:8080

mcp_servers:
  - name: kubectl
    url: http://mcp-kubectl:8080/sse
    authorization_headers:
      Authorization: /path/to/token
```

---

## Section 1: Workflows and Agent Runs

### Capability Matrix

| Capability | spawn:none | spawn:local | spawn:ephemeral | Status |
|---|:---:|:---:|:---:|---|
| LLM inference | Yes | Yes | Yes | Done |
| @step_tool functions | Yes | Yes | n/a | Done |
| MCP servers | Yes | Yes | Yes | Done |
| Skills | Yes | Yes | Yes (OCI) | Done |
| Streaming | Yes | fallback | n/a | Done |
| Output schemas | Yes | Yes | Yes | Done |
| Step chaining | Yes | Yes | Yes | Done |
| Human approval | Yes | Yes | Yes | Done |
| OTEL tracing | Yes | Yes | Yes | Done |
| Transcript capture | Yes | Yes | Yes | Done |
| RAG / file_search | - | - | - | **Gap** |
| Shields / guardrails | - | - | - | **Gap** |
| Quota enforcement | - | - | - | **Gap** |
| Conversation history | basic | - | - | **Partial** |

### Workflow Execution E2E Verified

All cloud-agents example workflows execute successfully through lightspeed-stack:

| Workflow | Steps | Spawn modes | Result |
|---|---|---|---|
| triage-classify | 3 (agent, approval, agent) | none | Pass |
| local-investigate | 4 (none, local, approval, local) | none+local | Pass |
| security-audit | 3+ | none | Pass |
| diagnostic | varies | none | Pass |
| All 10 definitions | varies | varies | Parse + first step passes |

### Remaining Gaps

**RAG / file_search** -- Not available in any spawn mode. Recommended path: deploy a vector search service as an MCP server. Steps declare `mcp_servers: [rag-service]`. No executor changes needed.

**Shields / guardrails** -- Input validation (question validity, PII redaction) not wired. Recommended: StepMiddleware (cloud-agents #144) for pre/post hooks, or endpoint-level shields as a simpler interim.

**Quota enforcement** -- Per-user token quotas not enforced. Infrastructure exists in lightspeed-stack (`src/quota/`). Wire as StepMiddleware or at endpoint level.

---

## Section 2: ChatWorkflowRunner -- Becoming the Chat Agent

### The Unification

The existing `/query` chatbot is a `spawn: none` agent with conversation management, MCP tools, RAG, shields, and streaming. Cloud-agents' planned `ChatWorkflowRunner` (#145) replaces the internal execution path.

```mermaid
graph LR
    subgraph today["Today: /query"]
        q1["/query request"] --> ba["build_agent()"]
        ba --> ls["Llama Stack"]
        ls --> mcp_proxy["MCP via proxy"]
        ls --> llm_ls["LLM"]
    end

    subgraph future["Target: /query via ChatWorkflowRunner"]
        q2["/query request"] --> cwr["ChatWorkflowRunner"]
        cwr --> cs["ConversationStore\nload/save turns"]
        cwr --> mw["StepMiddleware\nshields + quota + tracing"]
        mw --> de2["DirectExecutor"]
        de2 --> pai2["pydantic-ai Agent"]
        pai2 --> mcp_native["MCP via MCPToolset"]
        pai2 --> llm_de["LLM"]
    end

    style today fill:#fff3e0,stroke:#ff9800
    style future fill:#e8f5e9,stroke:#4caf50
```

### What's Done

| Component | Status |
|---|---|
| Agent execution via DirectExecutor | Done |
| MCP tools via pydantic-ai MCPToolset | Done |
| Streaming via run_stream() | Done |
| Skills via pydantic-ai-skills | Done |
| OTEL tracing in executor | Done |
| StepMetadata with user_id | Done |
| conversation_id load/save | Done |
| API field compatibility with /query | Done |
| /v1/agent-tools listing | Done |

### Gaps to Close

| Gap | Blocker? | Owner | Dependency |
|---|---|---|---|
| **ConversationStore ABC** | Yes | cloud-agents #145 | Shared turn-level storage interface |
| **ChatWorkflowRunner** | Yes | cloud-agents #145 | Runner for dynamic user turns |
| **StepMiddleware** | Yes | cloud-agents #144 | Pre/post hooks for cross-cutting concerns |
| **Shield implementations** | No | lightspeed-stack | Wire existing capabilities as middleware |
| **RAG** | No | Both | MCP server or inline pre-processing |
| **Compaction** | No | lightspeed-stack | Summarize old turns when context fills |
| **Splunk telemetry** | No | lightspeed-stack | Backend-specific event dispatch |
| **System prompt from config** | No | lightspeed-stack | One-line fallback to customization.system_prompt |

### Migration Path

```mermaid
graph LR
    p1["Phase 1\n/query/direct\n endpoint"]
    p2["Phase 2\nstreaming"]
    p3["Phase 3\nconversation_id"]
    p4["Phase 4\nChatWorkflow\nRunner"]
    p5["Phase 5\nStepMiddleware"]
    p6["Phase 6\nshields +\nquota"]
    p7["Phase 7\nRAG"]
    p8["Phase 8\nswitch /query\ninternals"]
    p9["Phase 9\nremove\nLlama Stack"]

    p1 --> p2 --> p3 --> p4 --> p5 --> p6 --> p7 --> p8 --> p9

    style p1 fill:#c8e6c9,stroke:#4caf50
    style p2 fill:#c8e6c9,stroke:#4caf50
    style p3 fill:#c8e6c9,stroke:#4caf50
    style p4 fill:#fff9c4,stroke:#fbc02d
    style p5 fill:#fff9c4,stroke:#fbc02d
    style p6 fill:#ffecb3,stroke:#ff9800
    style p7 fill:#ffecb3,stroke:#ff9800
    style p8 fill:#ffecb3,stroke:#ff9800
    style p9 fill:#ffecb3,stroke:#ff9800
```

Green = done. Yellow = waiting on cloud-agents. Orange = pending lightspeed-stack work.

The external API (`/query` request/response shape) remains unchanged throughout. Callers are unaffected.

### Dependency Chain

```mermaid
graph TD
    ca145["cloud-agents #145\nConversationStore +\nChatWorkflowRunner"]
    ca144["cloud-agents #144\nStepMiddleware"]

    shields["lightspeed-stack\nwire shields"]
    quota["lightspeed-stack\nwire quota"]
    otel["lightspeed-stack\nwire OTEL"]
    rag["lightspeed-stack\nRAG via MCP"]
    switch["lightspeed-stack\nswitch /query"]
    remove["lightspeed-stack\nremove Llama Stack"]

    ca145 --> ca144
    ca144 --> shields
    ca144 --> quota
    ca144 --> otel
    ca145 --> switch
    rag --> switch
    shields --> switch
    quota --> switch
    switch --> remove

    style ca145 fill:#e3f2fd,stroke:#2196f3
    style ca144 fill:#e3f2fd,stroke:#2196f3
    style shields fill:#fff3e0,stroke:#ff9800
    style quota fill:#fff3e0,stroke:#ff9800
    style otel fill:#fff3e0,stroke:#ff9800
    style rag fill:#fff3e0,stroke:#ff9800
    style switch fill:#ffebee,stroke:#f44336
    style remove fill:#ffebee,stroke:#f44336
```

Blue = cloud-agents. Orange = lightspeed-stack. Red = final migration steps.

---

## Scaling

| Component | Multi-pod safe? | Notes |
|---|---|---|
| `/query/direct` | Yes | Stateless per call |
| `/agents/run` | Yes | Stateless per call |
| Conversation state | Yes (PostgreSQL) | Shared database |
| Workflow state | Yes (PostgreSQL) | Shared database |
| Running workflow tasks | No (in-memory) | Use Temporal for crash recovery |
| MCP connections | Yes | Opened/closed per step |
