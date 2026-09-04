#!/usr/bin/env python3
"""Tiny single-tool MCP server for the cloud-agents demo.

Backs the `agent-none-tools` / `workflow-none-tools` scenarios in
docs/cloud-agents-demo-curl.sh: it exposes one deterministic tool,
`get_pod_status`, so an in-process (spawn:none) agent loop has a real
external tool to call. The canned answer is intentionally specific
(restarts=7, OOMKilled) so you can tell from the model's reply -- and from
the resulting Jaeger trace's `execute_tool get_pod_status` span -- that the
tool was actually invoked rather than hallucinated.

Uses the FastMCP server bundled with the `mcp` SDK (mcp.server.fastmcp),
not the slim standalone `fastmcp` package (which omits server support).

Usage:
    # Runs streamable-http on http://127.0.0.1:9111/mcp by default.
    uv run python docs/cloud-agents-demo-mcp.py

    # Override host/port:
    DEMO_MCP_HOST=0.0.0.0 DEMO_MCP_PORT=9200 uv run python docs/cloud-agents-demo-mcp.py

Then point a run at it, e.g. in the demo script:
    mcp_servers: [{"name": "pod-status", "url": "http://localhost:9111/mcp"}]
"""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

HOST = os.environ.get("DEMO_MCP_HOST", "127.0.0.1")
PORT = int(os.environ.get("DEMO_MCP_PORT", "9111"))

mcp = FastMCP("pod-status-demo", host=HOST, port=PORT)


@mcp.tool()
def get_pod_status(pod_name: str) -> str:
    """Return the current status of a Kubernetes pod by name.

    Parameters:
        pod_name: The name of the pod to look up.

    Returns:
        A short human-readable status string. memoryLimit is a deliberately
        odd, non-default value (347Mi) that a model won't emit on its own --
        so seeing it echoed back proves the tool was actually called rather
        than the answer hallucinated.
    """
    return (
        f"Pod {pod_name}: phase=Running, restarts=7, "
        "lastState=CrashLoopBackOff, reason=OOMKilled, memoryLimit=347Mi"
    )


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
