"""PoC: Agent Skills with progressive disclosure using pydantic-ai-skills.

Uses the repo's example skills at examples/skills/ (openshift-troubleshooting, code-review).
The agent discovers and loads skills on demand — not stuffed into the system prompt upfront.

Install: uv pip install pydantic-ai-skills
Run:     uv run python playground/try_skills.py
"""

import asyncio
from pathlib import Path

from pydantic_ai import Agent
from pydantic_ai_skills import SkillsCapability

import sys; sys.path.append(str(__import__("pathlib").Path(__file__).resolve().parent.parent))
from playground.common import make_model

SKILLS_DIR = str(Path(__file__).resolve().parent.parent / "examples" / "skills")


async def skill_discovery() -> None:
    """Agent discovers and uses a skill to answer a domain question."""
    print("=== Skill Discovery ===")
    agent = Agent(
        make_model(),
        defer_model_check=True,
        instructions=(
            "You are a helpful assistant. "
            "When the user asks about a topic, check if you have a relevant skill "
            "using the list_skills tool first, then load it with load_skill."
        ),
        capabilities=[SkillsCapability(directories=[SKILLS_DIR])],
    )

    result = await agent.run(
        "My pods are stuck in CrashLoopBackOff on OpenShift. What should I do?"
    )
    
    print(result.output)
    print()


async def progressive_disclosure() -> None:
    """Show that skills are loaded on demand, not upfront.

    First question doesn't need a skill. Second triggers skill loading.
    """
    print("=== Progressive Disclosure ===")
    agent = Agent(
        make_model(),
        defer_model_check=True,
        instructions=(
            "You are a helpful assistant. "
            "Use your skills tools when the user's question matches a skill's domain. "
            "For general questions, answer directly without loading skills."
        ),
        capabilities=[SkillsCapability(directories=[SKILLS_DIR])],
    )

    result1 = await agent.run("What is 2 + 2?")
    
    print(f"General question (no skill needed): {result1.output}")
    print()

    result2 = await agent.run(
        "I need to review a pull request. What should I check?",
        message_history=result1.all_messages(),
    )
    print(f"Domain question (code-review skill loaded): {result2.output}")
    print()


async def skill_discovery_with_steps() -> None:
    """Same as skill_discovery but with full step-by-step visibility via agent.iter()."""
    from pydantic_ai._agent_graph import CallToolsNode, ModelRequestNode, UserPromptNode
    from pydantic_ai.messages import TextPart, ToolCallPart, ToolReturnPart
    from pydantic_graph import End

    print("=== Skill Discovery (Step-by-Step) ===")
    print()

    agent = Agent(
        make_model(),
        defer_model_check=True,
        instructions=(
            "You are a helpful assistant. "
            "When the user asks about a topic, check if you have a relevant skill "
            "using the list_skills tool first, then load it with load_skill."
        ),
        capabilities=[SkillsCapability(directories=[SKILLS_DIR])],
    )

    step = 0
    async with agent.iter(
        "My pods are stuck in CrashLoopBackOff on OpenShift. What should I do?"
    ) as run:
        async for node in run:
            if isinstance(node, UserPromptNode):
                print(f"[user] {node.user_prompt}")
                print()
            elif isinstance(node, CallToolsNode):
                for part in node.model_response.parts:
                    if isinstance(part, ToolCallPart):
                        step += 1
                        args = part.args if len(str(part.args)) < 80 else str(part.args)[:80] + "..."
                        print(f"  step {step} [tool call] {part.tool_name}({args})")
                    elif isinstance(part, TextPart):
                        print()
                        print(f"  step {step + 1} [final answer]")
                        print()
                        print(part.content)
            elif isinstance(node, ModelRequestNode):
                for part in node.request.parts:
                    if isinstance(part, ToolReturnPart):
                        content = str(part.content)
                        if len(content) > 200:
                            content = content[:200] + "..."
                        print(f"         [result]  {part.tool_name} -> {content}")
            elif isinstance(node, End):
                pass

    print()

async def skill_discovery_with_live_logs() -> None:
    """Run the skill discovery example with clean live logs via event_stream_handler."""
    from pydantic_ai.messages import (
        FinalResultEvent,
        FunctionToolCallEvent,
        FunctionToolResultEvent,
        PartDeltaEvent,
        PartStartEvent,
        TextPartDelta,
    )

    print("=== Skill Discovery (Live Logs) ===")
    print()

    agent = Agent(
        make_model(),
        defer_model_check=True,
        instructions=(
            "You are a helpful assistant. "
            "When the user asks about a topic, check if you have a relevant skill "
            "using the list_skills tool first, then load it with load_skill."
        ),
        capabilities=[SkillsCapability(directories=[SKILLS_DIR])],
    )

    text_started = False
    step = 0

    async def log_events(ctx, events) -> None:
        nonlocal text_started, step
        del ctx

        async for event in events:
            if isinstance(event, FunctionToolCallEvent):
                step += 1
                args = str(event.part.args)
                if len(args) > 120:
                    args = args[:120] + "..."
                print(f"[step {step}] tool call: {event.part.tool_name}({args})")

            elif isinstance(event, FunctionToolResultEvent):
                content = str(event.part.content)
                if len(content) > 240:
                    content = content[:240] + "..."
                print(f"[step {step}] tool result: {event.part.tool_name} -> {content}")

            elif isinstance(event, PartStartEvent):
                part = event.part
                part_kind = getattr(part, "part_kind", None)
                if part_kind == "text":
                    if not text_started:
                        step += 1
                        text_started = True
                        print(f"\n[step {step}] final answer:")
                    content = getattr(part, "content", "")
                    if content:
                        print(content, end="", flush=True)

            elif isinstance(event, PartDeltaEvent):
                delta = event.delta
                if isinstance(delta, TextPartDelta):
                    if not text_started:
                        step += 1
                        text_started = True
                        print(f"\n[step {step}] final answer:")
                    print(delta.content_delta, end="", flush=True)

            elif isinstance(event, FinalResultEvent):
                print("\n\n[final result detected]")

    result = await agent.run(
        "My pods are stuck in CrashLoopBackOff on OpenShift. What should I do?",
        event_stream_handler=log_events,
    )

    print("\n=== Final Output ===")
    print(result.output)
    print()


async def main() -> None:
    """Run all skills examples."""
    await skill_discovery()
    # await progressive_disclosure()
    # await skill_discovery_with_steps()
    # await skill_discovery_with_live_logs()
    await skill_discovery_with_live_logs()


if __name__ == "__main__":
    asyncio.run(main())
