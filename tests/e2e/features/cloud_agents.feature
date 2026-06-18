@cloud_agents
Feature: Cloud Agents cross-pod communication

  Tests that the diagnostic agent pod responds correctly to HTTP requests
  and can execute multi-step diagnostic workflows.

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
