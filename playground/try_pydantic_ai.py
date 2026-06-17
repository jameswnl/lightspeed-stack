"""Playground: exercise LlamaStackProvider or OpenAI with pydantic-ai."""

import asyncio
import os

from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIResponsesModel

import sys; sys.path.append(str(__import__("pathlib").Path(__file__).resolve().parent.parent))
from playground.common import make_model


async def basic_chat_real_openai() -> None:
    """Real OpenAI API — no custom provider, no defer_model_check needed."""
    agent = Agent(
        "openai:gpt-4o-mini",
        instructions="Answer concisely in 2-3 sentences.",
    )

    result = await agent.run("What is Ansible and why would someone use it?")
    print("=== Basic Chat (Real OpenAI) ===")
    print(result.output)
    print()


async def basic_chat_openai() -> None:
    """Direct OpenAI provider pointing at Ollama — no LlamaStackProvider."""
    from pydantic_ai.providers.openai import OpenAIProvider

    base_url = os.environ.get("OLLAMA_URL", "http://localhost:11434/v1")
    model_name = os.environ.get("PLAYGROUND_MODEL", "qwen3.6:latest")
    provider = OpenAIProvider(base_url=base_url, api_key="not-needed")
    model = OpenAIResponsesModel(model_name, provider=provider)
    agent = Agent(
        model,
        defer_model_check=True,
        instructions="Answer concisely in 2-3 sentences.",
    )

    result = await agent.run("What is Ansible and why would someone use it?")
    print("=== Basic Chat (OpenAI → Ollama) ===")
    print(result.output)
    print()


async def basic_chat() -> None:
    """Simple single-turn chat through the LlamaStackProvider."""
    agent = Agent(
        make_model(),
        defer_model_check=True,
        instructions="Answer concisely in 2-3 sentences.",
    )

    result = await agent.run("What is Ansible and why would someone use it?")
    print("=== Basic Chat ===")
    print(result.output)
    print()


async def multi_turn() -> None:
    """Multi-turn conversation reusing message history."""
    agent = Agent(
        make_model(),
        defer_model_check=True,
        instructions="Answer concisely in 2-3 sentences.",
    )

    result1 = await agent.run("What is a playbook in Ansible?")
    print("=== Multi-Turn ===")
    print(f"Turn 1: {result1.output}")
    print()

    result2 = await agent.run(
        "How is that different from a role?",
        message_history=result1.all_messages(),
    )
    print(f"Turn 2: {result2.output}")
    print()


async def with_tool() -> None:
    """Agent with a custom tool — the LLM decides when to call it."""
    agent = Agent(
        make_model(),
        defer_model_check=True,
        instructions="Use tools when available. Answer concisely.",
    )

    @agent.tool_plain
    def get_ansible_version() -> str:
        """Return the current Ansible version."""
        return "ansible-core 2.18.1"

    result = await agent.run("What version of Ansible is installed?")
    print("=== With Tool ===")
    print(result.output)
    print()


async def structured_output() -> None:
    """Agent that returns structured data via a Pydantic model."""
    from pydantic import BaseModel

    class ToolSummary(BaseModel):
        name: str
        category: str
        one_liner: str

    agent = Agent(
        make_model(),
        defer_model_check=True,
        output_type=ToolSummary,
    )

    result = await agent.run("Describe Ansible Tower")
    print("=== Structured Output ===")
    print(f"  name:      {result.output.name}")
    print(f"  category:  {result.output.category}")
    print(f"  one_liner: {result.output.one_liner}")
    print()


async def streaming() -> None:
    """Stream tokens as they arrive — mirrors the /streaming_query path."""
    import sys

    agent = Agent(
        make_model(),
        defer_model_check=True,
        instructions="Explain in 3-4 sentences.",
    )

    print("=== Streaming ===")
    async with agent.run_stream("What is an Ansible collection?") as stream:
        async for chunk in stream.stream_text(delta=True):
            sys.stdout.write(chunk)
            sys.stdout.flush()
    print("\n")


async def main() -> None:
    """Run all playground examples."""
    await basic_chat_real_openai()
    await basic_chat_openai()
    await basic_chat()
    await multi_turn()
    await with_tool()
    await structured_output()
    await streaming()


if __name__ == "__main__":
    asyncio.run(main())
