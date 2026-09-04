#!/usr/bin/env bash
# Live-demo curl commands covering the full /v1/agents/run and
# /v1/workflows/run endpoint x spawn-mode matrix, matching
# tests/e2e/cloud_agents/test_agents_run_http_e2e.py and
# test_workflows_http_e2e.py.
#
# agent-none / agent-ephemeral / workflow-ephemeral-approval illustrate
# the three tabs in docs/cloud-agents-integration.html. The rest
# (agent-local, workflow-none-approval, workflow-local, workflow-ephemeral)
# round out the same matrix for parity with the automated e2e suite --
# they aren't part of that illustration, just additional scenarios:
#   agent-none                 POST /v1/agents/run,    spawn: "none"
#   agent-none-tools            POST /v1/agents/run,    spawn: "none",      + MCP tool call
#   agent-local                 POST /v1/agents/run,    spawn: "local"
#   agent-ephemeral             POST /v1/agents/run,    spawn: "ephemeral"
#   workflow-ephemeral-approval POST /v1/workflows/run, spawn: "ephemeral", multi-step + approval
#   workflow-none-approval      POST /v1/workflows/run, spawn: "none",      multi-step + approval
#   workflow-none-tools          POST /v1/workflows/run, spawn: "none",      single step + MCP tool call
#   workflow-local               POST /v1/workflows/run, spawn: "local",     single step
#   workflow-ephemeral           POST /v1/workflows/run, spawn: "ephemeral", single step, no approval
#
# Note: agent-local and workflow-local omit output_schema -- the
# cloud-agents SubprocessExecutor behind spawn:local has no native
# structured-output mode yet (jameswnl/lightspeed-cloud-agents#235), so
# it can't reliably guarantee schema-conforming JSON the way spawn:none
# and spawn:ephemeral can.
#
# The *-none-tools scenarios exercise a real in-process (spawn:none) agent
# loop that calls an external MCP tool. They need the companion demo MCP
# server running first:
#   uv run python docs/cloud-agents-demo-mcp.py    # serves get_pod_status on :9111
# Override its URL with DEMO_MCP_URL (default http://localhost:9111/mcp).
# workflow-none-tools additionally requires this repo's mcp_servers field on
# POST /v1/workflows/run (RunWorkflowRequest.mcp_servers).
#
# Usage:
#   BASE_URL=http://localhost:8090 ./docs/cloud-agents-demo-curl.sh agent-none
#   BASE_URL=http://localhost:8090 ./docs/cloud-agents-demo-curl.sh agent-none-tools
#   BASE_URL=http://localhost:8090 ./docs/cloud-agents-demo-curl.sh agent-local
#   BASE_URL=http://localhost:8090 ./docs/cloud-agents-demo-curl.sh agent-ephemeral
#   BASE_URL=http://localhost:8090 ./docs/cloud-agents-demo-curl.sh workflow-ephemeral-approval
#   BASE_URL=http://localhost:8090 ./docs/cloud-agents-demo-curl.sh workflow-none-approval
#   BASE_URL=http://localhost:8090 ./docs/cloud-agents-demo-curl.sh workflow-none-tools
#   BASE_URL=http://localhost:8090 ./docs/cloud-agents-demo-curl.sh workflow-local
#   BASE_URL=http://localhost:8090 ./docs/cloud-agents-demo-curl.sh workflow-ephemeral
#   BASE_URL=http://localhost:8090 ./docs/cloud-agents-demo-curl.sh discover
#
# Auth: if the deployment uses authentication.module: "noop" (the harness
# default), no Authorization header is needed. If it uses k8s/JWK auth,
# export TOKEN=<bearer-token> and it will be attached automatically.

set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8090}"
DEMO_MCP_URL="${DEMO_MCP_URL:-http://localhost:9111/mcp}"
AUTH_HEADER=()
if [[ -n "${TOKEN:-}" ]]; then
  AUTH_HEADER=(-H "Authorization: Bearer $TOKEN")
fi

discover() {
  echo "== Registered agent tools (spawn:none/local) =="
  curl -s "${AUTH_HEADER[@]+"${AUTH_HEADER[@]}"}" "$BASE_URL/v1/agent-tools" | jq
  echo
  echo "== Registered MCP servers =="
  curl -s "${AUTH_HEADER[@]+"${AUTH_HEADER[@]}"}" "$BASE_URL/v1/mcp-servers" | jq
}

agent_none() {
  echo "== Agent — In-Process (spawn: none) =="
  local resp status
  resp=$(curl -s -w '\n%{http_code}' -X POST "$BASE_URL/v1/agents/run" \
    "${AUTH_HEADER[@]+"${AUTH_HEADER[@]}"}" \
    -H "Content-Type: application/json" \
    -d '{
      "prompt": "Is pod checkout-7f9 healthy?",
      "spawn": "none",
      "provider": "openai",
      "model": "gpt-4o-mini",
      "tools": [],
      "mcp_servers": null,
      "output_schema": {
        "type": "object",
        "properties": { "healthy": {"type": "boolean"}, "reason": {"type": "string"} },
        "required": ["healthy", "reason"]
      }
    }')
  status="${resp##*$'\n'}"
  echo "${resp%$'\n'*}" | jq
  [[ "$status" -lt 400 ]]
}

agent_none_tools() {
  echo "== Agent — In-Process + MCP tool (spawn: none) =="
  echo "   (needs 'uv run python docs/cloud-agents-demo-mcp.py' at $DEMO_MCP_URL)"
  local resp status
  resp=$(curl -s -w '\n%{http_code}' -X POST "$BASE_URL/v1/agents/run" \
    "${AUTH_HEADER[@]+"${AUTH_HEADER[@]}"}" \
    -H "Content-Type: application/json" \
    -d '{
      "prompt": "Use the get_pod_status tool to look up pod checkout-7f9, then report its exact memory limit and restart count.",
      "spawn": "none",
      "provider": "openai",
      "model": "gpt-4o-mini",
      "tools": [],
      "mcp_servers": [{"name": "pod-status", "url": "'"$DEMO_MCP_URL"'"}]
    }')
  status="${resp##*$'\n'}"
  echo "${resp%$'\n'*}" | jq
  [[ "$status" -lt 400 ]]
}

agent_local() {
  echo "== Agent — Subprocess (spawn: local) =="
  local resp status
  resp=$(curl -s -w '\n%{http_code}' -X POST "$BASE_URL/v1/agents/run" \
    "${AUTH_HEADER[@]+"${AUTH_HEADER[@]}"}" \
    -H "Content-Type: application/json" \
    -d '{
      "prompt": "Say one sentence confirming pod checkout-7f9 is healthy.",
      "spawn": "local",
      "provider": "openai",
      "model": "gpt-4o-mini",
      "tools": [],
      "mcp_servers": null
    }')
  status="${resp##*$'\n'}"
  echo "${resp%$'\n'*}" | jq
  [[ "$status" -lt 400 ]]
}

agent_ephemeral() {
  echo "== Agent — OpenShell (spawn: ephemeral) =="
  local resp status
  resp=$(curl -s -w '\n%{http_code}' -X POST "$BASE_URL/v1/agents/run" \
    "${AUTH_HEADER[@]+"${AUTH_HEADER[@]}"}" \
    -H "Content-Type: application/json" \
    -d '{
      "prompt": "Is pod checkout-7f9 healthy?",
      "spawn": "ephemeral",
      "provider": "openai",
      "model": "gpt-4o-mini",
      "mcp_servers": [{"name": "kubectl-mcp", "url": "http://kubectl-mcp:8000/mcp"}]
    }')
  status="${resp##*$'\n'}"
  echo "${resp%$'\n'*}" | jq
  [[ "$status" -lt 400 ]]
}

wait_for_status() {
  # Poll GET /v1/workflows/$1 until .status is one of $2 (space-separated),
  # up to $3 seconds (default 30). Prints the final response and exits
  # non-zero on timeout -- POST /v1/workflows/run returns 202 as soon as
  # the workflow task is created, before triage or pause; approving/
  # checking immediately after races the async execution. Callers with a
  # spawn:ephemeral or spawn:local step should pass a higher budget (150,
  # matching the pytest e2e suite) -- sandbox boot + LLM latency routinely
  # exceeds 30s.
  #
  # Sets WORKFLOW_STATUS (intentionally global) to the last-seen status so
  # callers can require_status() it without a redundant GET.
  local wf_id="$1" wanted="$2" max="${3:-30}" resp=""
  WORKFLOW_STATUS=""
  for _ in $(seq 1 "$max"); do
    resp=$(curl -sf "${AUTH_HEADER[@]+"${AUTH_HEADER[@]}"}" "$BASE_URL/v1/workflows/$wf_id")
    WORKFLOW_STATUS=$(echo "$resp" | jq -r .status)
    if [[ " $wanted " == *" $WORKFLOW_STATUS "* ]]; then
      echo "$resp" | jq
      return 0
    fi
    sleep 1
  done
  echo "ERROR: workflow '$wf_id' never reached status in [$wanted] within ${max}s (last: $WORKFLOW_STATUS)" >&2
  echo "$resp" | jq
  return 1
}

require_status() {
  # Fail with a clear error unless WORKFLOW_STATUS (set by the most recent
  # wait_for_status call) equals the expected value. $2 is a verb phrase
  # for the error message, e.g. "pause for approval" or "complete successfully".
  local expected="$1" verb="$2"
  if [[ "$WORKFLOW_STATUS" != "$expected" ]]; then
    echo "ERROR: workflow did not $verb (status: $WORKFLOW_STATUS)" >&2
    exit 1
  fi
}

workflow_ephemeral_approval() {
  echo "== Workflow — OpenShell + approval (spawn: ephemeral, POST /v1/workflows/run) =="
  local resp wf_id

  resp=$(curl -sf -X POST "$BASE_URL/v1/workflows/run" \
    "${AUTH_HEADER[@]+"${AUTH_HEADER[@]}"}" \
    -H "Content-Type: application/json" \
    -d '{
      "definition": {
        "apiVersion": "v1",
        "kind": "AgentWorkflow",
        "metadata": {"name": "triage-remediate-demo"},
        "spec": {
          "steps": [
            {
              "name": "triage", "type": "agent", "spawn": "ephemeral",
              "output_key": "triage_result",
              "prompt": "Diagnose the checkout-7f9 pod issue. Report severity and root cause.",
              "output_schema": {
                "type": "object",
                "properties": {"severity": {"type": "string"}, "root_cause": {"type": "string"}},
                "required": ["severity", "root_cause"]
              },
              "timeout_seconds": 120
            },
            {
              "name": "approve", "type": "human-approval",
              "output_key": "approval",
              "message": "Root cause: {{ steps.triage_result.output.root_cause }}. Approve remediation?",
              "risk_level": "high"
            },
            {
              "name": "remediate", "type": "agent", "spawn": "ephemeral",
              "output_key": "remediate_result",
              "prompt": "Apply the fix for: {{ steps.triage_result.output.root_cause }}",
              "condition": "steps.approval.output.approved == true",
              "timeout_seconds": 120
            }
          ]
        }
      },
      "provider": {"name": "openai", "model": "gpt-4o-mini"}
    }')
  echo "$resp" | jq
  wf_id=$(echo "$resp" | jq -r .workflow_id)
  if [[ -z "$wf_id" || "$wf_id" == "null" ]]; then
    echo "ERROR: no workflow_id in response" >&2
    exit 1
  fi
  echo "workflow_id=$wf_id"

  echo
  echo "-- Waiting for status 'paused' at 'approve' --"
  wait_for_status "$wf_id" "paused failed cancelled completed" 150
  require_status "paused" "pause for approval"

  echo
  echo "-- Approving 'approve' step --"
  curl -sf -X POST "$BASE_URL/v1/workflows/$wf_id/approve" \
    "${AUTH_HEADER[@]+"${AUTH_HEADER[@]}"}" \
    -H "Content-Type: application/json" \
    -d '{"step_name": "approve", "decision": "approved", "approver": "demo-user"}' | jq

  echo
  echo "-- Waiting for a terminal status --"
  wait_for_status "$wf_id" "completed failed cancelled" 150
  require_status "completed" "complete successfully"

  echo
  echo "-- Per-step transcripts --"
  curl -sf "${AUTH_HEADER[@]+"${AUTH_HEADER[@]}"}" "$BASE_URL/v1/workflows/$wf_id/transcripts" | jq
}

workflow_none_approval() {
  echo "== Workflow — In-Process + approval (spawn: none, POST /v1/workflows/run) =="
  local resp wf_id

  resp=$(curl -sf -X POST "$BASE_URL/v1/workflows/run" \
    "${AUTH_HEADER[@]+"${AUTH_HEADER[@]}"}" \
    -H "Content-Type: application/json" \
    -d '{
      "definition": {
        "apiVersion": "v1",
        "kind": "AgentWorkflow",
        "metadata": {"name": "triage-remediate-none-demo"},
        "spec": {
          "steps": [
            {
              "name": "triage", "type": "agent", "spawn": "none",
              "output_key": "triage_result",
              "prompt": "Diagnose the checkout-7f9 pod issue. Report severity and root cause.",
              "output_schema": {
                "type": "object",
                "properties": {"severity": {"type": "string"}, "root_cause": {"type": "string"}},
                "required": ["severity", "root_cause"]
              },
              "timeout_seconds": 120
            },
            {
              "name": "approve", "type": "human-approval",
              "output_key": "approval",
              "message": "Root cause: {{ steps.triage_result.output.root_cause }}. Approve remediation?",
              "risk_level": "high"
            },
            {
              "name": "remediate", "type": "agent", "spawn": "none",
              "output_key": "remediate_result",
              "prompt": "Say one sentence confirming the fix for: {{ steps.triage_result.output.root_cause }}",
              "condition": "steps.approval.output.approved == true",
              "timeout_seconds": 120
            }
          ]
        }
      },
      "provider": {"name": "openai", "model": "gpt-4o-mini"}
    }')
  echo "$resp" | jq
  wf_id=$(echo "$resp" | jq -r .workflow_id)
  if [[ -z "$wf_id" || "$wf_id" == "null" ]]; then
    echo "ERROR: no workflow_id in response" >&2
    exit 1
  fi
  echo "workflow_id=$wf_id"

  echo
  echo "-- Waiting for status 'paused' at 'approve' --"
  wait_for_status "$wf_id" "paused failed cancelled completed"
  require_status "paused" "pause for approval"

  echo
  echo "-- Approving 'approve' step --"
  curl -sf -X POST "$BASE_URL/v1/workflows/$wf_id/approve" \
    "${AUTH_HEADER[@]+"${AUTH_HEADER[@]}"}" \
    -H "Content-Type: application/json" \
    -d '{"step_name": "approve", "decision": "approved", "approver": "demo-user"}' | jq

  echo
  echo "-- Waiting for a terminal status --"
  wait_for_status "$wf_id" "completed failed cancelled"
  require_status "completed" "complete successfully"

  echo
  echo "-- Per-step transcripts --"
  curl -sf "${AUTH_HEADER[@]+"${AUTH_HEADER[@]}"}" "$BASE_URL/v1/workflows/$wf_id/transcripts" | jq
}

workflow_none_tools() {
  echo "== Workflow — In-Process + MCP tool (spawn: none, POST /v1/workflows/run) =="
  echo "   (needs 'uv run python docs/cloud-agents-demo-mcp.py' at $DEMO_MCP_URL)"
  local resp wf_id

  resp=$(curl -sf -X POST "$BASE_URL/v1/workflows/run" \
    "${AUTH_HEADER[@]+"${AUTH_HEADER[@]}"}" \
    -H "Content-Type: application/json" \
    -d '{
      "definition": {
        "apiVersion": "v1",
        "kind": "AgentWorkflow",
        "metadata": {"name": "none-tools-demo"},
        "spec": {
          "steps": [
            {
              "name": "check", "type": "agent", "spawn": "none",
              "output_key": "check_result",
              "prompt": "Use the get_pod_status tool to look up pod checkout-7f9. Report its exact memory limit and restart count verbatim.",
              "mcp_servers": ["pod-status"],
              "timeout_seconds": 120
            }
          ]
        }
      },
      "provider": {"name": "openai", "model": "gpt-4o-mini"},
      "mcp_servers": [{"name": "pod-status", "url": "'"$DEMO_MCP_URL"'"}]
    }')
  echo "$resp" | jq
  wf_id=$(echo "$resp" | jq -r .workflow_id)
  if [[ -z "$wf_id" || "$wf_id" == "null" ]]; then
    echo "ERROR: no workflow_id in response" >&2
    exit 1
  fi
  echo "workflow_id=$wf_id"

  echo
  echo "-- Waiting for a terminal status --"
  wait_for_status "$wf_id" "completed failed cancelled" 150
  require_status "completed" "complete successfully"

  echo
  echo "-- Per-step transcripts (look for the tool's 347Mi value) --"
  curl -sf "${AUTH_HEADER[@]+"${AUTH_HEADER[@]}"}" "$BASE_URL/v1/workflows/$wf_id/transcripts" | jq
}

workflow_local() {
  echo "== Workflow — Subprocess (spawn: local, POST /v1/workflows/run) =="
  local resp wf_id

  resp=$(curl -sf -X POST "$BASE_URL/v1/workflows/run" \
    "${AUTH_HEADER[@]+"${AUTH_HEADER[@]}"}" \
    -H "Content-Type: application/json" \
    -d '{
      "definition": {
        "apiVersion": "v1",
        "kind": "AgentWorkflow",
        "metadata": {"name": "investigate-local-demo"},
        "spec": {
          "steps": [
            {
              "name": "investigate", "type": "agent", "spawn": "local",
              "output_key": "investigate_result",
              "prompt": "Say one sentence confirming the checkout-7f9 pod is healthy.",
              "timeout_seconds": 120
            }
          ]
        }
      },
      "provider": {"name": "openai", "model": "gpt-4o-mini"}
    }')
  echo "$resp" | jq
  wf_id=$(echo "$resp" | jq -r .workflow_id)
  if [[ -z "$wf_id" || "$wf_id" == "null" ]]; then
    echo "ERROR: no workflow_id in response" >&2
    exit 1
  fi
  echo "workflow_id=$wf_id"

  echo
  echo "-- Waiting for a terminal status --"
  wait_for_status "$wf_id" "completed failed cancelled" 150
  require_status "completed" "complete successfully"

  echo
  echo "-- Per-step transcripts --"
  curl -sf "${AUTH_HEADER[@]+"${AUTH_HEADER[@]}"}" "$BASE_URL/v1/workflows/$wf_id/transcripts" | jq
}

workflow_ephemeral() {
  echo "== Workflow — OpenShell, no approval (spawn: ephemeral, POST /v1/workflows/run) =="
  local resp wf_id

  resp=$(curl -sf -X POST "$BASE_URL/v1/workflows/run" \
    "${AUTH_HEADER[@]+"${AUTH_HEADER[@]}"}" \
    -H "Content-Type: application/json" \
    -d '{
      "definition": {
        "apiVersion": "v1",
        "kind": "AgentWorkflow",
        "metadata": {"name": "investigate-ephemeral-demo"},
        "spec": {
          "steps": [
            {
              "name": "investigate", "type": "agent", "spawn": "ephemeral",
              "output_key": "investigate_result",
              "prompt": "Say one sentence confirming the checkout-7f9 pod is healthy.",
              "timeout_seconds": 120
            }
          ]
        }
      },
      "provider": {"name": "openai", "model": "gpt-4o-mini"}
    }')
  echo "$resp" | jq
  wf_id=$(echo "$resp" | jq -r .workflow_id)
  if [[ -z "$wf_id" || "$wf_id" == "null" ]]; then
    echo "ERROR: no workflow_id in response" >&2
    exit 1
  fi
  echo "workflow_id=$wf_id"

  echo
  echo "-- Waiting for a terminal status --"
  wait_for_status "$wf_id" "completed failed cancelled" 150
  require_status "completed" "complete successfully"

  echo
  echo "-- Per-step transcripts --"
  curl -sf "${AUTH_HEADER[@]+"${AUTH_HEADER[@]}"}" "$BASE_URL/v1/workflows/$wf_id/transcripts" | jq
}

case "${1:-}" in
  discover) discover ;;
  agent-none) agent_none ;;
  agent-none-tools) agent_none_tools ;;
  agent-local) agent_local ;;
  agent-ephemeral) agent_ephemeral ;;
  workflow-ephemeral-approval) workflow_ephemeral_approval ;;
  workflow-none-approval) workflow_none_approval ;;
  workflow-none-tools) workflow_none_tools ;;
  workflow-local) workflow_local ;;
  workflow-ephemeral) workflow_ephemeral ;;
  *)
    echo "Usage: $0 {discover|agent-none|agent-none-tools|agent-local|agent-ephemeral|workflow-ephemeral-approval|workflow-none-approval|workflow-none-tools|workflow-local|workflow-ephemeral}" >&2
    exit 1
    ;;
esac
