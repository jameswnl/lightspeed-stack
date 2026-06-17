"""PoC: Agentic loop — the agent reasons through multiple tool calls autonomously.

The agent has access to several tools and must chain them together to answer
a question. Pydantic AI handles the loop: LLM → tool call → LLM → tool call → ... → answer.

Run: uv run python playground/try_agent_loop.py
"""

import asyncio
import sys

from pydantic_ai import Agent, RunContext

import sys; sys.path.append(str(__import__("pathlib").Path(__file__).resolve().parent.parent))
from playground.common import make_model


# --- Simulated infrastructure data ---

HOSTS = {
    "web-01": {"role": "webserver", "ip": "10.0.1.10", "os": "RHEL 9"},
    "web-02": {"role": "webserver", "ip": "10.0.1.11", "os": "RHEL 9"},
    "db-01": {"role": "database", "ip": "10.0.2.10", "os": "RHEL 9"},
    "cache-01": {"role": "cache", "ip": "10.0.3.10", "os": "RHEL 8"},
}

HOST_STATUS = {
    "web-01": {"cpu": 45, "memory": 62, "disk": 78, "status": "healthy"},
    "web-02": {"cpu": 92, "memory": 88, "disk": 45, "status": "degraded"},
    "db-01": {"cpu": 30, "memory": 55, "disk": 91, "status": "warning"},
    "cache-01": {"cpu": 15, "memory": 40, "disk": 30, "status": "healthy"},
}

RECENT_EVENTS = {
    "web-02": [
        "2026-06-09T14:30:00Z - CPU spike detected (92%)",
        "2026-06-09T14:25:00Z - Deployment started: app-v2.3.1",
        "2026-06-09T14:20:00Z - Health check latency > 2s",
    ],
    "db-01": [
        "2026-06-09T13:00:00Z - Disk usage crossed 90% threshold",
        "2026-06-09T12:45:00Z - Slow query detected (>5s)",
    ],
}


# --- Agent with tools ---

agent = Agent(
    make_model(),
    defer_model_check=True,
    instructions=(
        "You are an infrastructure diagnostic agent. "
        "To investigate an issue, use the available tools step by step: "
        "1) List or look up hosts to find the relevant ones. "
        "2) Check their status for resource usage. "
        "3) Check recent events for context. "
        "Then provide your diagnosis and recommendation. "
        "Always use the tools — do not guess at host data."
    ),
)


@agent.tool_plain
def list_hosts() -> list[dict]:
    """List all known hosts with their role, IP, and OS."""
    return [{"hostname": k, **v} for k, v in HOSTS.items()]


@agent.tool_plain
def get_host_status(hostname: str) -> dict:
    """Get CPU, memory, disk usage and health status for a host."""
    if hostname not in HOST_STATUS:
        return {"error": f"Unknown host: {hostname}"}
    return {"hostname": hostname, **HOST_STATUS[hostname]}


@agent.tool_plain
def get_recent_events(hostname: str) -> list[str]:
    """Get recent events/alerts for a host. Returns empty list if no events."""
    return RECENT_EVENTS.get(hostname, [])


# --- Examples ---


async def diagnose_issue() -> None:
    """Agent chains multiple tool calls to diagnose an infrastructure issue."""
    result = await agent.run(
        "Users are reporting slow response times from our web application. "
        "Investigate and tell me what's going on."
    )
    print("=== Agent Loop: Diagnose Issue ===")
    print(result.output)

    msgs = result.all_messages()
    tool_calls = [
        part.tool_name
        for msg in msgs
        for part in getattr(msg, "parts", [])
        if hasattr(part, "tool_name")
    ]
    print(f"\nTool calls made: {tool_calls}")
    print(f"Total LLM round-trips: {len([m for m in msgs if hasattr(m, 'parts') and any(hasattr(p, 'content') for p in m.parts)])}")
    print()


async def diagnose_streaming() -> None:
    """Same diagnostic flow but with streaming — see the agent think in real-time."""
    print("=== Agent Loop: Streaming Diagnosis ===")
    async with agent.run_stream(
        "The database server seems to be running out of disk space. "
        "Check which host is affected and what's happening."
    ) as stream:
        async for chunk in stream.stream_text(delta=True):
            sys.stdout.write(chunk)
            sys.stdout.flush()
    print("\n")


async def proactive_check() -> None:
    """Agent proactively checks all hosts and reports any concerns."""
    result = await agent.run(
        "Do a health check across all hosts. "
        "Report any hosts that need attention and why."
    )
    print("=== Agent Loop: Proactive Health Check ===")
    print(result.output)
    print()


async def streaming_with_tool_visibility() -> None:
    """Stream with full visibility into each step — tool calls shown as they happen."""
    from pydantic_ai._agent_graph import CallToolsNode, ModelRequestNode, UserPromptNode
    from pydantic_ai.messages import (
        TextPart,
        ToolCallPart,
        ToolReturnPart,
    )
    from pydantic_graph import End

    print("=== Agent Loop: Streaming with Tool Visibility ===")
    print()

    async with agent.iter(
        "Check if any hosts have disk issues and recommend a fix."
    ) as run:
        async for node in run:
            if isinstance(node, UserPromptNode):
                print(f"[user] {node.user_prompt}")
                print()
            elif isinstance(node, CallToolsNode):
                for part in node.model_response.parts:
                    if isinstance(part, ToolCallPart):
                        print(f"[tool call] {part.tool_name}({part.args})")
                    elif isinstance(part, TextPart):
                        print()
                        print("[answer]")
                        print(part.content)
            elif isinstance(node, ModelRequestNode):
                for part in node.request.parts:
                    if isinstance(part, ToolReturnPart):
                        content = str(part.content)
                        if len(content) > 120:
                            content = content[:120] + "..."
                        print(f"[tool result] {part.tool_name} -> {content}")
            elif isinstance(node, End):
                pass

    print()


async def main() -> None:
    """Run all agent loop examples."""
    await diagnose_issue()
    await diagnose_streaming()
    await proactive_check()
    await streaming_with_tool_visibility()


if __name__ == "__main__":
    asyncio.run(main())
