"""Unit tests for RunWorkflowRequest model."""

from models.api.requests.agents import RunWorkflowRequest

_MINIMAL_DEFINITION = {
    "apiVersion": "v1",
    "kind": "AgentWorkflow",
    "metadata": {"name": "test-wf"},
    "spec": {"steps": []},
}


class TestRunWorkflowRequest:
    """Test cases for the RunWorkflowRequest model."""

    def test_session_id_defaults_to_none(self) -> None:
        """session_id is optional and defaults to None when omitted."""
        req = RunWorkflowRequest(definition=_MINIMAL_DEFINITION)
        assert req.session_id is None

    def test_session_id_accepts_provided_value(self) -> None:
        """session_id is stored as given when the caller supplies one."""
        req = RunWorkflowRequest(
            definition=_MINIMAL_DEFINITION,
            session_id="ses-abc123",
        )
        assert req.session_id == "ses-abc123"

    def test_empty_session_id_normalizes_to_none(self) -> None:
        """An empty-string session_id is treated the same as omitted."""
        req = RunWorkflowRequest(
            definition=_MINIMAL_DEFINITION,
            session_id="",
        )
        assert req.session_id is None

    def test_mcp_servers_defaults_to_none(self) -> None:
        """mcp_servers is optional and defaults to None when omitted."""
        req = RunWorkflowRequest(definition=_MINIMAL_DEFINITION)
        assert req.mcp_servers is None

    def test_mcp_servers_accepts_config_dicts(self) -> None:
        """mcp_servers stores the caller-supplied list of server configs.

        Each entry is a dict ({name, url, headers}) -- run-scoped MCP
        catalog that spawn:none steps connect to via pydantic-ai
        MCPToolset. This mirrors AgentRunRequest.mcp_servers so a
        single-step workflow behaves like POST /v1/agents/run.
        """
        servers = [
            {"name": "pod-status", "url": "http://localhost:9111/mcp"},
            {"name": "kubectl", "url": "http://kubectl-mcp:8000/mcp"},
        ]
        req = RunWorkflowRequest(
            definition=_MINIMAL_DEFINITION,
            mcp_servers=servers,
        )
        assert req.mcp_servers == servers
