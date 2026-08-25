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
