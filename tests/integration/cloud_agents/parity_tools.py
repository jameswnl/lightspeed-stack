"""Deterministic step tool for transcript-parity integration tests.

Imported in-process (spawn: none, via DirectExecutor's tools_module) and
in the subprocess child (spawn: local, via CLOUD_AGENTS_TOOLS_MODULE) so
the same logical tool call runs under every spawn mode. The mock LLM
server scripts the call arguments, making the tool's transcript payload
identical across modes.
"""

from __future__ import annotations

from cloud_agents.workflow.executor.step.tools import step_tool


@step_tool("parity_probe", description="Deterministic probe tool for parity tests")
def parity_probe(q: int = 0) -> str:
    """Return a deterministic string embedding the query argument.

    Parameters:
        q: Integer argument scripted by the mock LLM server.

    Returns:
        Deterministic tool output string.
    """
    return f"parity:{q}"
