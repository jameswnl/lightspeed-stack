"""PoC: Multi-agent patterns — delegation and programmatic hand-off.

Demonstrates two ways to compose agents:
1. Delegation: A router agent calls a specialist sub-agent via @agent.tool
2. Hand-off: Application code runs agents in sequence, passing structured data between them

Run: uv run python playground/try_multi_agent.py
"""

import asyncio

from pydantic import BaseModel
from pydantic_ai import Agent, RunContext
from pydantic_ai.usage import UsageLimits

import sys; sys.path.append(str(__import__("pathlib").Path(__file__).resolve().parent.parent))
from playground.common import make_model


# --- Specialist sub-agents (used by the router) ---

ansible_agent = Agent(
    make_model(),
    defer_model_check=True,
    instructions=(
        "You are an Ansible expert. Answer questions about Ansible "
        "playbooks, roles, modules, and best practices. Be concise (2-3 sentences)."
    ),
)

openshift_agent = Agent(
    make_model(),
    defer_model_check=True,
    instructions=(
        "You are an OpenShift expert. Answer questions about pods, deployments, "
        "routes, operators, and troubleshooting. Be concise (2-3 sentences)."
    ),
)


async def delegation() -> None:
    """Router agent delegates to specialist sub-agents via tools."""
    router = Agent(
        make_model(),
        defer_model_check=True,
        instructions=(
            "You are a routing agent. Based on the user's question, "
            "call the appropriate specialist tool: ask_ansible_expert or ask_openshift_expert. "
            "Return the specialist's answer directly."
        ),
    )

    @router.tool
    async def ask_ansible_expert(ctx: RunContext[None], question: str) -> str:
        """Delegate to the Ansible specialist for Ansible-related questions."""
        result = await ansible_agent.run(question, usage=ctx.usage)
        return result.output

    @router.tool
    async def ask_openshift_expert(ctx: RunContext[None], question: str) -> str:
        """Delegate to the OpenShift specialist for OpenShift-related questions."""
        result = await openshift_agent.run(question, usage=ctx.usage)
        return result.output

    result = await router.run("How do I scale a deployment in OpenShift?")
    print("=== Delegation ===")
    print(f"Answer: {result.output}")
    print(f"Total tokens: {result.usage.total_tokens}")
    print()


async def handoff() -> None:
    """Two agents run in sequence — first extracts data, second acts on it."""

    class IssueAnalysis(BaseModel):
        category: str
        severity: str
        summary: str

    analyzer = Agent(
        make_model(),
        defer_model_check=True,
        output_type=IssueAnalysis,
        instructions=(
            "Analyze the reported issue and classify it. "
            "Category: one of 'networking', 'storage', 'compute', 'security'. "
            "Severity: one of 'low', 'medium', 'high', 'critical'."
        ),
    )

    responder = Agent(
        make_model(),
        defer_model_check=True,
        instructions="Given an issue analysis, provide 2-3 actionable troubleshooting steps.",
    )

    issue = (
        "Our production pods can't reach the database service. "
        "The connection times out after 30 seconds. "
        "This started after we applied a new NetworkPolicy."
    )

    analysis = await analyzer.run(issue)
    print("=== Programmatic Hand-off ===")
    print(f"Step 1 — Analysis:")
    print(f"  category: {analysis.output.category}")
    print(f"  severity: {analysis.output.severity}")
    print(f"  summary:  {analysis.output.summary}")
    print()

    response = await responder.run(
        f"Issue category: {analysis.output.category}, "
        f"severity: {analysis.output.severity}. "
        f"Summary: {analysis.output.summary}. "
        f"Original report: {issue}"
    )
    print(f"Step 2 — Response:")
    print(response.output)
    print()


async def main() -> None:
    """Run all multi-agent examples."""
    await delegation()
    await handoff()


if __name__ == "__main__":
    asyncio.run(main())
