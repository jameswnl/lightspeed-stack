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
#   agent-local                 POST /v1/agents/run,    spawn: "local"
#   agent-ephemeral             POST /v1/agents/run,    spawn: "ephemeral", k8s-diag skill + Landlock demo
#   workflow-ephemeral-approval POST /v1/workflows/run, spawn: "ephemeral", multi-step + approval
#   workflow-none-approval      POST /v1/workflows/run, spawn: "none",      multi-step + approval
#   workflow-local               POST /v1/workflows/run, spawn: "local",     single step
#   workflow-ephemeral           POST /v1/workflows/run, spawn: "ephemeral", single step, no approval
#
# Note: agent-local and workflow-local omit output_schema -- the
# cloud-agents SubprocessExecutor behind spawn:local has no native
# structured-output mode yet (jameswnl/lightspeed-cloud-agents#235), so
# it can't reliably guarantee schema-conforming JSON the way spawn:none
# and spawn:ephemeral can.
#
# MCP server reachability: agent-none and agent-local run in-process on
# this machine, not inside the cluster, so they reach the mock pod-status
# MCP server (deployed via ~/ws/local-infra's ocp-prod-mcp-pod-status-*
# targets) at localhost:8084 -- port-forward it first:
#   oc -n openshell-prod port-forward svc/mcp-pod-status-mock 8084:8084
# agent-ephemeral runs inside an OpenShell sandbox pod on the cluster, so
# it reaches the same service via in-cluster DNS instead
# (mcp-pod-status-mock:8084), no port-forward needed.
#
# Usage:
#   BASE_URL=http://localhost:8090 ./docs/cloud-agents-demo-curl.sh agent-none
#   BASE_URL=http://localhost:8090 ./docs/cloud-agents-demo-curl.sh agent-local
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
WF_ID=""

discover() {
  echo "== Registered agent tools (spawn:none/local) =="
  curl -s "${AUTH_HEADER[@]+"${AUTH_HEADER[@]}"}" "$BASE_URL/v1/agent-tools" | jq
  echo
  echo "== Registered MCP servers =="
  curl -s "${AUTH_HEADER[@]+"${AUTH_HEADER[@]}"}" "$BASE_URL/v1/mcp-servers" | jq
}

run_agent() {
  # POST an /v1/agents/run payload, pretty-print the body, and fail on
  # HTTP >= 400. $1: title line, $2: spawn mode, $3: prompt, $4: extra
  # JSON object merged onto the shared {prompt, spawn, provider, model}
  # base -- callers only spell out what differs per spawn mode.
  local title="$1" spawn="$2" prompt="$3" extra="{}"
  if [[ $# -ge 4 ]]; then
    extra="$4"
  fi
  echo "$title"
  local payload resp status
  payload=$(jq -n \
    --arg prompt "$prompt" \
    --arg spawn "$spawn" \
    --argjson extra "$extra" \
    '{prompt: $prompt, spawn: $spawn, provider: "openai", model: "gpt-5-mini"} + $extra')
  resp=$(curl -s -w '\n%{http_code}' -X POST "$BASE_URL/v1/agents/run" \
    "${AUTH_HEADER[@]+"${AUTH_HEADER[@]}"}" \
    -H "Content-Type: application/json" \
    -d "$payload")
  status="${resp##*$'\n'}"
  echo "${resp%$'\n'*}" | jq
  [[ "$status" -lt 400 ]]
}

agent_none() {
  run_agent "== Agent — In-Process (spawn: none) ==" "none" \
    "Is pod checkout-7f9 healthy?" \
    '{
      "tools": [],
      "mcp_servers": [{"name": "kubectl-mcp", "url": "http://localhost:8084/mcp"}],
      "output_schema": {
        "type": "object",
        "properties": { "healthy": {"type": "boolean"}, "reason": {"type": "string"} },
        "required": ["healthy", "reason"]
      }
    }'
}

agent_local() {
  run_agent "== Agent — Subprocess (spawn: local) ==" "local" \
    "Check whether pod checkout-7f9 is healthy and say one sentence confirming the result." \
    '{"tools": [], "mcp_servers": [{"name": "kubectl-mcp", "url": "http://localhost:8084/mcp"}]}'
}

agent_ephemeral() {
  # allowed_skills=["k8s-diag"]: the spawner materializes just that skill
  # into the sandbox and Landlock-grants /skills/k8s-diag, so the prompt
  # below demonstrates both sides -- the allowed skill works, and reading
  # an unlisted skill (/skills/security-audit) is denied at the filesystem
  # boundary. Requires those skills baked into the sandbox image (/skills).
  run_agent "== Agent — OpenShell (spawn: ephemeral, k8s-diag skill + Landlock) ==" "ephemeral" \
    "Use the k8s-diag skill to check whether pod checkout-7f9 is healthy. Then try reading /skills/security-audit/SKILL.md and report whether that read succeeded or was denied, and why." \
    '{"mcp_servers": [{"name": "kubectl-mcp", "url": "http://mcp-pod-status-mock:8084/mcp"}], "provider": "openai", "model": "gpt-5-mini"}'
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

approval_workflow_payload() {
  # Triage -> human-approval -> remediate definition shared by both
  # approval demos. $1: spawn mode, $2: workflow name, $3: full remediate
  # prompt (including the {{ steps.triage_result... }} template ref).
  jq -n \
    --arg spawn "$1" \
    --arg name "$2" \
    --arg remediate_prompt "$3" \
    '{
      "definition": {
        "apiVersion": "v1",
        "kind": "AgentWorkflow",
        "metadata": {"name": $name},
        "spec": {
          "steps": [
            {
              "name": "triage", "type": "agent", "spawn": $spawn,
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
              "name": "remediate", "type": "agent", "spawn": $spawn,
              "output_key": "remediate_result",
              "prompt": $remediate_prompt,
              "condition": "steps.approval.output.approved == true",
              "timeout_seconds": 120
            }
          ]
        }
      },
      "provider": {"name": "openai", "model": "gpt-5-mini"}
    }'
}

single_step_workflow_payload() {
  # Single investigate-step definition shared by the non-approval demos.
  # $1: spawn mode, $2: workflow name.
  jq -n \
    --arg spawn "$1" \
    --arg name "$2" \
    '{
      "definition": {
        "apiVersion": "v1",
        "kind": "AgentWorkflow",
        "metadata": {"name": $name},
        "spec": {
          "steps": [
            {
              "name": "investigate", "type": "agent", "spawn": $spawn,
              "output_key": "investigate_result",
              "prompt": "Say one sentence confirming the checkout-7f9 pod is healthy.",
              "timeout_seconds": 120
            }
          ]
        }
      },
      "provider": {"name": "openai", "model": "gpt-5-mini"}
    }'
}

submit_workflow() {
  # POST $1 to /v1/workflows/run, print the response, and set WF_ID
  # (intentionally global, mirroring WORKFLOW_STATUS) to the created
  # workflow id. Exits non-zero when the response carries no id.
  local payload="$1" resp
  resp=$(curl -sf -X POST "$BASE_URL/v1/workflows/run" \
    "${AUTH_HEADER[@]+"${AUTH_HEADER[@]}"}" \
    -H "Content-Type: application/json" \
    -d "$payload")
  echo "$resp" | jq
  WF_ID=$(echo "$resp" | jq -r .workflow_id)
  if [[ -z "$WF_ID" || "$WF_ID" == "null" ]]; then
    echo "ERROR: no workflow_id in response" >&2
    exit 1
  fi
  echo "workflow_id=$WF_ID"
}

finish_workflow() {
  # Wait for a terminal status and print per-step transcripts.
  # $1: workflow id, $2: wait budget in seconds (default 30).
  local wf_id="$1" budget="${2:-30}"
  echo
  echo "-- Waiting for a terminal status --"
  wait_for_status "$wf_id" "completed failed cancelled" "$budget"
  require_status "completed" "complete successfully"

  echo
  echo "-- Per-step transcripts --"
  curl -sf "${AUTH_HEADER[@]+"${AUTH_HEADER[@]}"}" "$BASE_URL/v1/workflows/$wf_id/transcripts" | jq
}

approval_cycle() {
  # Pause -> approve -> finish flow shared by both approval demos.
  # $1: workflow id, $2: wait budget in seconds (default 30).
  local wf_id="$1" budget="${2:-30}"
  echo
  echo "-- Waiting for status 'paused' at 'approve' --"
  wait_for_status "$wf_id" "paused failed cancelled completed" "$budget"
  require_status "paused" "pause for approval"

  echo
  echo "-- Approving 'approve' step --"
  curl -sf -X POST "$BASE_URL/v1/workflows/$wf_id/approve" \
    "${AUTH_HEADER[@]+"${AUTH_HEADER[@]}"}" \
    -H "Content-Type: application/json" \
    -d '{"step_name": "approve", "decision": "approved", "approver": "demo-user"}' | jq

  finish_workflow "$wf_id" "$budget"
}

workflow_ephemeral_approval() {
  echo "== Workflow — OpenShell + approval (spawn: ephemeral, POST /v1/workflows/run) =="
  submit_workflow "$(approval_workflow_payload "ephemeral" "triage-remediate-demo" \
    "Apply the fix for: {{ steps.triage_result.output.root_cause }}")"
  approval_cycle "$WF_ID" 150
}

workflow_none_approval() {
  echo "== Workflow — In-Process + approval (spawn: none, POST /v1/workflows/run) =="
  submit_workflow "$(approval_workflow_payload "none" "triage-remediate-none-demo" \
    "Say one sentence confirming the fix for: {{ steps.triage_result.output.root_cause }}")"
  approval_cycle "$WF_ID" 30
}

workflow_local() {
  echo "== Workflow — Subprocess (spawn: local, POST /v1/workflows/run) =="
  submit_workflow "$(single_step_workflow_payload "local" "investigate-local-demo")"
  finish_workflow "$WF_ID" 150
}

workflow_ephemeral() {
  echo "== Workflow — OpenShell, no approval (spawn: ephemeral, POST /v1/workflows/run) =="
  submit_workflow "$(single_step_workflow_payload "ephemeral" "investigate-ephemeral-demo")"
  finish_workflow "$WF_ID" 150
}

case "${1:-}" in
  discover) discover ;;
  agent-none) agent_none ;;
  agent-local) agent_local ;;
  agent-ephemeral) agent_ephemeral ;;
  workflow-ephemeral-approval) workflow_ephemeral_approval ;;
  workflow-none-approval) workflow_none_approval ;;
  workflow-local) workflow_local ;;
  workflow-ephemeral) workflow_ephemeral ;;
  *)
    echo "Usage: $0 {discover|agent-none|agent-local|agent-ephemeral|workflow-ephemeral-approval|workflow-none-approval|workflow-local|workflow-ephemeral}" >&2
    exit 1
    ;;
esac
