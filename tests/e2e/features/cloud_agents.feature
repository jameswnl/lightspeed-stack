@cloud_agents
Feature: Cloud Agents cross-pod communication

  Tests that diagnostic and monitoring agent pods respond correctly
  to HTTP requests and can collaborate across containers.

  # ============================================================
  # Phase 1a: Diagnostic agent direct tests
  # ============================================================

  Background:
    Given The diagnostic agent pod is running
    And The diagnostic agent healthcheck returns 200

  Scenario: Diagnostic agent health check
    When I GET the diagnostic agent "/healthz"
    Then The response status is 200
    And The response body contains "ready"
    And The response body contains "diagnostic-agent"

  @slow
  Scenario: Diagnostic agent responds to /v1/run
    When I POST to the diagnostic agent "/v1/run" with prompt "Check all hosts for issues"
    Then The response status is 200
    And The response contains a valid AgentRunResponse
    And The output field "cluster_healthy" is present

  Scenario: Diagnostic agent rejects empty prompt
    When I POST to the diagnostic agent "/v1/run" with empty prompt
    Then The response status is 422

  @slow
  Scenario: Diagnostic agent diagnoses and reports
    When I POST to the diagnostic agent "/v1/run" with prompt "Do a full health check of the cluster. Inspect all hosts and report your findings."
    Then The response status is 200
    And The response contains a valid AgentRunResponse
    And The output field "cluster_healthy" is present
    And The output field "issues_found" is present
    And The output field "summary" is present

  # ============================================================
  # Phase 1b: Monitoring agent + cross-pod scenarios
  # ============================================================

  Scenario: Monitoring agent health check
    Given The monitoring agent pod is running
    When I GET the monitoring agent "/healthz"
    Then The response status is 200
    And The response body contains "ready"
    And The response body contains "monitoring-agent"

  @slow
  Scenario: Monitoring agent detects anomaly on degraded cluster
    Given The monitoring agent pod is running
    When I POST to the monitoring agent "/v1/run" with prompt "Check all hosts for issues"
    Then The response status is 200
    And The response contains a valid AgentRunResponse
    And The output field "cluster_healthy" is present

  Scenario: Diagnostic agent async submit and poll
    When I submit async to the diagnostic agent "/v1/run" with prompt "Check hosts"
    Then The async submit response status is 202
    And The async submit response contains a run_id
    When I poll the diagnostic agent for the run_id
    Then The poll response has status "running" or "completed"

  Scenario: Diagnostic agent liveness check
    When I GET the diagnostic agent "/livez"
    Then The response status is 200
    And The response body contains "alive"

  Scenario: Diagnostic agent metrics endpoint
    When I GET the diagnostic agent "/metrics"
    Then The response status is 200
    And The response body contains "ls_agent_runs_total"
