#!/usr/bin/env bash
# Live-demo curl commands covering the full /v1/agents/run and
# /v1/workflows/run endpoint x spawn-mode matrix, matching
# tests/e2e/cloud_agents/test_agents_run_http_e2e.py and
# test_workflows_http_e2e.py.
#
# agent-none / agent-ephemeral / workflow-ephemeral-approval illustrate
# the three tabs in docs/cloud-agents-integration.html. The rest
# (workflow-none-approval, workflow-local, workflow-ephemeral) round out
# the same matrix for parity with the automated e2e suite -- they aren't
# part of that illustration, just additional scenarios:
#   agent-none                 POST /v1/agents/run,    spawn: "none"
#   agent-ephemeral             POST /v1/agents/run,    spawn: "ephemeral"
#   workflow-ephemeral-approval POST /v1/workflows/run, spawn: "ephemeral", multi-step + approval
#   workflow-none-approval      POST /v1/workflows/run, spawn: "none",      multi-step + approval
#   workflow-local               POST /v1/workflows/run, spawn: "local",     single step
#   workflow-ephemeral           POST /v1/workflows/run, spawn: "ephemeral", single step, no approval
#
# Usage:
#   BASE_URL=http://localhost:8090 ./docs/cloud-agents-demo-curl.sh agent-none
#   BASE_URL=http://localhost:8090 ./docs/cloud-agents-demo-curl.sh agent-ephemeral
#   BASE_URL=http://localhost:8090 ./docs/cloud-agents-demo-curl.sh workflow-ephemeral-approval
#   BASE_URL=http://localhost:8090 ./docs/cloud-agents-demo-curl.sh workflow-none-approval
#   BASE_URL=http://localhost:8090 ./docs/cloud-agents-demo-curl.sh workflow-local
#   BASE_URL=http://localhost:8090 ./docs/cloud-agents-demo-curl.sh workflow-ephemeral
#   BASE_URL=http://localhost:8090 ./docs/cloud-agents-demo-curl.sh discover
#
# Auth: if the deployment uses authentication.module: "noop" (the harness
# default), no Authorization header is needed. If it uses k8s/JWK auth,
# export TOKEN=<bearer-token> and it will be attached automatically.

set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8090}"
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
  curl -s -X POST "$BASE_URL/v1/agents/run" \
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
    }' | jq
}

agent_ephemeral() {
  echo "== Agent — OpenShell (spawn: ephemeral) =="
  curl -s -X POST "$BASE_URL/v1/agents/run" \
    "${AUTH_HEADER[@]+"${AUTH_HEADER[@]}"}" \
    -H "Content-Type: application/json" \
    -d '{
      "prompt": "Is pod checkout-7f9 healthy?",
      "spawn": "ephemeral",
      "provider": "openai",
      "model": "gpt-4o-mini",
      "mcp_servers": [{"name": "kubectl-mcp", "url": "http://kubectl-mcp:8000/mcp"}]
    }' | jq
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
  local wf_id="$1" wanted="$2" max="${3:-30}" status="" resp=""
  for _ in $(seq 1 "$max"); do
    resp=$(curl -sf "${AUTH_HEADER[@]+"${AUTH_HEADER[@]}"}" "$BASE_URL/v1/workflows/$wf_id")
    status=$(echo "$resp" | jq -r .status)
    if [[ " $wanted " == *" $status "* ]]; then
      echo "$resp" | jq
      return 0
    fi
    sleep 1
  done
  echo "ERROR: workflow '$wf_id' never reached status in [$wanted] within ${max}s (last: $status)" >&2
  echo "$resp" | jq
  return 1
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
  status=$(curl -sf "${AUTH_HEADER[@]+"${AUTH_HEADER[@]}"}" "$BASE_URL/v1/workflows/$wf_id" | jq -r .status)
  if [[ "$status" != "paused" ]]; then
    echo "ERROR: workflow did not pause for approval (status: $status)" >&2
    exit 1
  fi

  echo
  echo "-- Approving 'approve' step --"
  curl -sf -X POST "$BASE_URL/v1/workflows/$wf_id/approve" \
    "${AUTH_HEADER[@]+"${AUTH_HEADER[@]}"}" \
    -H "Content-Type: application/json" \
    -d '{"step_name": "approve", "decision": "approved", "approver": "demo-user"}' | jq

  echo
  echo "-- Waiting for a terminal status --"
  wait_for_status "$wf_id" "completed failed cancelled" 150

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
  status=$(curl -sf "${AUTH_HEADER[@]+"${AUTH_HEADER[@]}"}" "$BASE_URL/v1/workflows/$wf_id" | jq -r .status)
  if [[ "$status" != "paused" ]]; then
    echo "ERROR: workflow did not pause for approval (status: $status)" >&2
    exit 1
  fi

  echo
  echo "-- Approving 'approve' step --"
  curl -sf -X POST "$BASE_URL/v1/workflows/$wf_id/approve" \
    "${AUTH_HEADER[@]+"${AUTH_HEADER[@]}"}" \
    -H "Content-Type: application/json" \
    -d '{"step_name": "approve", "decision": "approved", "approver": "demo-user"}' | jq

  echo
  echo "-- Waiting for a terminal status --"
  wait_for_status "$wf_id" "completed failed cancelled"

  echo
  echo "-- Per-step transcripts --"
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

  echo
  echo "-- Per-step transcripts --"
  curl -sf "${AUTH_HEADER[@]+"${AUTH_HEADER[@]}"}" "$BASE_URL/v1/workflows/$wf_id/transcripts" | jq
}

case "${1:-}" in
  discover) discover ;;
  agent-none) agent_none ;;
  agent-ephemeral) agent_ephemeral ;;
  workflow-ephemeral-approval) workflow_ephemeral_approval ;;
  workflow-none-approval) workflow_none_approval ;;
  workflow-local) workflow_local ;;
  workflow-ephemeral) workflow_ephemeral ;;
  *)
    echo "Usage: $0 {discover|agent-none|agent-ephemeral|workflow-ephemeral-approval|workflow-none-approval|workflow-local|workflow-ephemeral}" >&2
    exit 1
    ;;
esac
