"""PoC: Replacing Goose with pydantic-ai in the OpenStackAssistant CRD (RHOSSTRAT-1276).

Demonstrates that lightspeed-stack + pydantic-ai can serve as the agent runtime
for the OpenStackAssistant pod, replacing Goose. Maps Goose capabilities to
pydantic-ai equivalents:

  Goose capability          → pydantic-ai equivalent
  ─────────────────────────────────────────────────────
  goose session (REPL)      → interactive_session() (demo 4)
  /cluster-health recipe    → run_skill_script("cluster-health.py") via skills
  oc/openstack CLI calls    → MCP tools (structured, typed, RBAC-safe)
  .goosehints context       → skills with progressive disclosure
  Provider config (Secret)  → LlamaStackProvider (same Lightspeed Stack backend)

Composes four in-process FastMCP servers matching RHOSSTRAT child tickets:
  - OpenStackClient MCP (RHOSSTRAT-981) — read-only cluster queries
  - Configuration MCP    (RHOSSTRAT-980) — service config inspection
  - Logs MCP             (RHOSSTRAT-962) — log retrieval and search
  - Upgrade MCP          (RHOSSTRAT-979) — pre-flight checks and upgrade plans

Run demos:     uv run python playground/try_rhoso_upgrade.py [1|2|3|4|all]
Interactive:   uv run python playground/try_rhoso_upgrade.py interactive
"""

import asyncio
import sys
from pathlib import Path

from pydantic_ai import Agent
from pydantic_ai._agent_graph import CallToolsNode, ModelRequestNode, UserPromptNode
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.messages import TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai_skills import SkillsCapability
from pydantic_graph import End

import sys; sys.path.append(str(__import__("pathlib").Path(__file__).resolve().parent.parent))
from playground.common import make_model
from playground.rhoso_mcp_server import (
    config_server,
    logs_server,
    openstackclient_server,
    upgrade_server,
)

SKILLS_DIR = str(Path(__file__).resolve().parent.parent / "examples" / "skills")


def make_agent() -> Agent:
    """Create an agent with four RHOSO MCP servers and upgrade skills.

    Mirrors the production architecture from RHOSSTRAT-872 child tickets:
    four separate MCP servers composed into a single agent, plus skills
    for progressive knowledge injection (replaces Goose's .goosehints + recipes).
    """
    return Agent(
        make_model(),
        defer_model_check=True,
        instructions=(
            "You are an RHOSO (Red Hat OpenStack Services on OpenShift) upgrade assistant "
            "running inside an OpenStackAssistant pod on the cluster.\n\n"
            "You have access to four MCP tool servers:\n"
            "- OpenStackClient: read-only queries (compute services, VMs, volumes, network agents)\n"
            "- Configuration: fetch service config files (nova, neutron, cinder) and CRs\n"
            "- Logs: retrieve and search OpenStack service logs\n"
            "- Upgrade: pre-flight checks, version compatibility, and upgrade plans\n\n"
            "You also have skills you can discover and load on demand. Use list_skills "
            "to see what's available, load_skill to get detailed instructions, "
            "read_skill_resource for reference docs, and run_skill_script for diagnostics.\n\n"
            "When asked about upgrades or cluster health:\n"
            "1. Use your MCP tools to fetch the current cluster state\n"
            "2. Load relevant skills for domain expertise\n"
            "3. Analyze the data and provide specific, actionable recommendations\n\n"
            "When asked for CLI commands, provide exact openstack/oc CLI commands "
            "with proper flags and arguments.\n\n"
            "Always check cluster state before recommending upgrade actions."
        ),
        toolsets=[
            MCPToolset(openstackclient_server),
            MCPToolset(config_server),
            MCPToolset(logs_server),
            MCPToolset(upgrade_server),
        ],
        capabilities=[SkillsCapability(directories=[SKILLS_DIR])],
    )


async def _run_with_steps(agent: Agent, prompt: str, message_history=None) -> tuple:
    """Run agent with step-by-step visibility. Returns (output, all_messages)."""
    step = 0
    async with agent:
        async with agent.iter(prompt, message_history=message_history) as run:
            async for node in run:
                if isinstance(node, UserPromptNode):
                    pass
                elif isinstance(node, CallToolsNode):
                    for part in node.model_response.parts:
                        if isinstance(part, ToolCallPart):
                            step += 1
                            args = str(part.args)
                            if len(args) > 80:
                                args = args[:80] + "..."
                            print(f"  step {step} [tool] {part.tool_name}({args})")
                        elif isinstance(part, TextPart):
                            pass
                elif isinstance(node, ModelRequestNode):
                    for part in node.request.parts:
                        if isinstance(part, ToolReturnPart):
                            content = str(part.content)
                            if len(content) > 150:
                                content = content[:150] + "..."
                            print(f"         -> {content}")
                elif isinstance(node, End):
                    pass

    return run.result.output, run.result.all_messages()


# ---------------------------------------------------------------------------
# Demo 1: Pre-Upgrade Analysis (MCP tools + skill activation)
# ---------------------------------------------------------------------------

async def pre_upgrade_analysis() -> None:
    """Agent uses MCP tools to fetch cluster state and identifies upgrade blockers."""
    print("=" * 70)
    print("  Demo 1: Pre-Upgrade Analysis")
    print("  Goose equivalent: /cluster-health recipe + manual oc commands")
    print("=" * 70)
    print()

    agent = make_agent()
    output, _ = await _run_with_steps(
        agent,
        "I want to upgrade my RHOSO cluster from version 18 to 19. "
        "Can you check the cluster state and tell me if we're ready to upgrade? "
        "Identify any blockers or issues that need to be resolved first.",
    )
    print()
    print("--- Agent Response ---")
    print(output)
    print()


# ---------------------------------------------------------------------------
# Demo 2: CLI Command Generation
# ---------------------------------------------------------------------------

async def cli_command_generation() -> None:
    """Agent generates correct openstack CLI commands from natural language."""
    print("=" * 70)
    print("  Demo 2: CLI Command Generation")
    print("  Goose equivalent: manual oc exec + openstack CLI")
    print("=" * 70)
    print()

    agent = make_agent()
    output, _ = await _run_with_steps(
        agent,
        "I need to migrate all VMs off compute-2.example.com before upgrading it. "
        "Give me the exact openstack CLI commands to disable the compute service, "
        "live-migrate the VMs, and then verify the node is empty.",
    )
    print()
    print("--- Agent Response ---")
    print(output)
    print()


# ---------------------------------------------------------------------------
# Demo 3: Upgrade Procedure Q&A (skill references)
# ---------------------------------------------------------------------------

async def upgrade_procedure_qa() -> None:
    """Agent answers upgrade procedure questions using skill references."""
    print("=" * 70)
    print("  Demo 3: Upgrade Procedure Q&A")
    print("  Goose equivalent: .goosehints context + recipe output")
    print("=" * 70)
    print()

    agent = make_agent()
    output, _ = await _run_with_steps(
        agent,
        "What's the correct sequence for upgrading the RHOSO control plane? "
        "I need to know what to back up first and how to update the operator.",
    )
    print()
    print("--- Agent Response ---")
    print(output)
    print()


# ---------------------------------------------------------------------------
# Demo 4: Interactive Session (replaces `goose session`)
# ---------------------------------------------------------------------------

async def interactive_session() -> None:
    """Multi-turn interactive REPL — replaces `oc exec -it pod -- goose session`.

    This demonstrates the same workflow an admin would have with Goose:
    connect to the pod, ask questions, drill down, and the agent maintains
    conversation context across turns.
    """
    print("=" * 70)
    print("  Interactive RHOSO Assistant")
    print("  Replaces: oc exec -it <pod> -- goose session")
    print("=" * 70)
    print()
    print("  Type your questions. The agent has access to cluster MCP tools")
    print("  and upgrade skills. Type 'exit' or Ctrl-C to quit.")
    print()

    agent = make_agent()
    history = None

    async with agent:
        while True:
            try:
                prompt = input("you> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nSession ended.")
                break

            if not prompt:
                continue
            if prompt.lower() in ("exit", "quit", "/exit"):
                print("Session ended.")
                break

            step = 0
            async with agent.iter(prompt, message_history=history) as run:
                async for node in run:
                    if isinstance(node, CallToolsNode):
                        for part in node.model_response.parts:
                            if isinstance(part, ToolCallPart):
                                step += 1
                                args = str(part.args)
                                if len(args) > 60:
                                    args = args[:60] + "..."
                                print(f"  [{part.tool_name}] {args}")
                    elif isinstance(node, ModelRequestNode):
                        for part in node.request.parts:
                            if isinstance(part, ToolReturnPart):
                                content = str(part.content)
                                if len(content) > 100:
                                    content = content[:100] + "..."
                                print(f"    -> {content}")

            history = run.result.all_messages()
            print()
            print(run.result.output)
            print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    """Run RHOSO upgrade PoC demos."""
    demo = sys.argv[1] if len(sys.argv) > 1 else "all"

    demos = {
        "1": pre_upgrade_analysis,
        "2": cli_command_generation,
        "3": upgrade_procedure_qa,
        "4": interactive_session,
        "interactive": interactive_session,
    }

    if demo == "all":
        for key in ("1", "2", "3"):
            await demos[key]()
        print("Tip: run with 'interactive' for a multi-turn session (replaces goose session)")
    elif demo in demos:
        await demos[demo]()
    else:
        print(f"Usage: {sys.argv[0]} [1|2|3|4|interactive|all]")
        print()
        print("  1            Pre-upgrade analysis (MCP tools + skills)")
        print("  2            CLI command generation")
        print("  3            Upgrade procedure Q&A (skill references)")
        print("  4/interactive  Interactive session (replaces goose session)")
        print("  all          Run demos 1-3 (default)")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
