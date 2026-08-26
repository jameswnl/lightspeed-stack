#!/usr/bin/env bash
# Live-demo curl commands for the three illustrated tabs in
# docs/cloud-agents-integration.html:
#   1. Agent — In-Process   (POST /v1/agents/run, spawn: "none")
#   2. Agent — OpenShell    (POST /v1/agents/run, spawn: "ephemeral")
#   3. Workflow — OpenShell (POST /v1/workflows/run, multi-step)
#
# Usage:
#   BASE_URL=http://localhost:8090 ./docs/cloud-agents-demo-curl.sh tab1
#   BASE_URL=http://localhost:8090 ./docs/cloud-agents-demo-curl.sh tab2
#   BASE_URL=http://localhost:8090 ./docs/cloud-agents-demo-curl.sh tab3
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

tab3_workflow() {
  echo "== Tab 3: Workflow — OpenShell (POST /v1/workflows/run) =="
  local resp wf_id

  resp=$(curl -s -X POST "$BASE_URL/v1/workflows/run" \
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
  echo "workflow_id=$wf_id"

  echo
  echo "-- Status (should show paused at 'approve') --"
  curl -s "${AUTH_HEADER[@]+"${AUTH_HEADER[@]}"}" "$BASE_URL/v1/workflows/$wf_id" | jq

  echo
  echo "-- Approving 'approve' step --"
  curl -s -X POST "$BASE_URL/v1/workflows/$wf_id/approve" \
    "${AUTH_HEADER[@]+"${AUTH_HEADER[@]}"}" \
    -H "Content-Type: application/json" \
    -d '{"step_name": "approve", "decision": "approved", "approver": "demo-user"}' | jq

  echo
  echo "-- Status (should show completed) --"
  curl -s "${AUTH_HEADER[@]+"${AUTH_HEADER[@]}"}" "$BASE_URL/v1/workflows/$wf_id" | jq

  echo
  echo "-- Per-step transcripts --"
  curl -s "${AUTH_HEADER[@]+"${AUTH_HEADER[@]}"}" "$BASE_URL/v1/workflows/$wf_id/transcripts" | jq
}

case "${1:-}" in
  discover) discover ;;
  tab1) tab1_in_process ;;
  tab2) tab2_openshell_agent ;;
  tab3) tab3_workflow ;;
  *)
    echo "Usage: $0 {discover|tab1|tab2|tab3}" >&2
    exit 1
    ;;
esac
