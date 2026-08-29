#!/usr/bin/env bash
# Live-demo curl commands for the three illustrated tabs in
# docs/cloud-agents-integration.html:
#   1. Agent — In-Process   (POST /v1/agents/run, spawn: "none")
#   2. Agent — OpenShell    (POST /v1/agents/run, spawn: "ephemeral")
#   3. Workflow — OpenShell (POST /v1/workflows/run, multi-step, spawn: "ephemeral" + approval)
#
# Tabs 4-6 round out the same endpoint x spawn-mode matrix covered by
# tests/e2e/cloud_agents/test_agents_run_http_e2e.py and
# test_workflows_http_e2e.py, for parity between this manual demo and the
# automated e2e suite -- they aren't part of the integration.html
# illustration, just additional scenarios:
#   4. Workflow — spawn: "none", multi-step + approval
#   5. Workflow — spawn: "local", single step
#   6. Workflow — spawn: "ephemeral", single step, no approval
#
# Usage:
#   BASE_URL=http://localhost:8090 ./docs/cloud-agents-demo-curl.sh tab1
#   BASE_URL=http://localhost:8090 ./docs/cloud-agents-demo-curl.sh tab2
#   BASE_URL=http://localhost:8090 ./docs/cloud-agents-demo-curl.sh tab3
#   BASE_URL=http://localhost:8090 ./docs/cloud-agents-demo-curl.sh tab4
#   BASE_URL=http://localhost:8090 ./docs/cloud-agents-demo-curl.sh tab5
#   BASE_URL=http://localhost:8090 ./docs/cloud-agents-demo-curl.sh tab6
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

tab1_in_process() {
  echo "== Tab 1: Agent — In-Process (spawn: none) =="
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

tab2_openshell_agent() {
  echo "== Tab 2: Agent — OpenShell (spawn: ephemeral) =="
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
  # up to 30s. Prints the final response and exits non-zero on timeout --
  # POST /v1/workflows/run returns 202 as soon as the workflow task is
  # created, before triage or pause; approving/checking immediately after
  # races the async execution.
  local wf_id="$1" wanted="$2" status="" resp=""
  for _ in $(seq 1 30); do
    resp=$(curl -sf "${AUTH_HEADER[@]+"${AUTH_HEADER[@]}"}" "$BASE_URL/v1/workflows/$wf_id")
    status=$(echo "$resp" | jq -r .status)
    if [[ " $wanted " == *" $status "* ]]; then
      echo "$resp" | jq
      return 0
    fi
    sleep 1
  done
  echo "ERROR: workflow '$wf_id' never reached status in [$wanted] (last: $status)" >&2
  echo "$resp" | jq
  return 1
}

tab3_workflow() {
  echo "== Tab 3: Workflow — OpenShell (POST /v1/workflows/run) =="
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
  wait_for_status "$wf_id" "paused"

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

tab4_workflow_none_approval() {
  echo "== Tab 4: Workflow — In-Process (spawn: none, POST /v1/workflows/run) =="
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
  wait_for_status "$wf_id" "paused"

  echo
  echo "-- Approving 'approve' step --"
  curl -sf -X POST "$BASE_URL/v1/workflows/$wf_id/approve" \
    "${AUTH_HEADER[@]+"${AUTH_HEADER[@]}"}" \
    -H "Content-Type: application/json" \
    -d '{"step_name": "approve", "decision": "approved", "approver": "demo-user"}' | jq

  echo
  echo "-- Waiting for a terminal status --"
  wait_for_status "$wf_id" "completed failed cancelled"
}

tab5_workflow_local() {
  echo "== Tab 5: Workflow — Subprocess (spawn: local, POST /v1/workflows/run) =="
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
  wait_for_status "$wf_id" "completed failed cancelled"
}

tab6_workflow_ephemeral() {
  echo "== Tab 6: Workflow — OpenShell, no approval (spawn: ephemeral, POST /v1/workflows/run) =="
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
  wait_for_status "$wf_id" "completed failed cancelled"
}

case "${1:-}" in
  discover) discover ;;
  tab1) tab1_in_process ;;
  tab2) tab2_openshell_agent ;;
  tab3) tab3_workflow ;;
  tab4) tab4_workflow_none_approval ;;
  tab5) tab5_workflow_local ;;
  tab6) tab6_workflow_ephemeral ;;
  *)
    echo "Usage: $0 {discover|tab1|tab2|tab3|tab4|tab5|tab6}" >&2
    exit 1
    ;;
esac
