"""PoC: Structured output with complex models and union types.

Demonstrates Pydantic AI returning typed, validated data:
1. Complex nested model (troubleshooting report)
2. Union output types — agent returns Solution or NeedMoreInfo, app branches on result

Run: uv run python playground/try_structured.py
"""

import asyncio
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIResponsesModel

from pydantic_ai_lightspeed.llamastack import LlamaStackProvider

OLLAMA_URL = "http://localhost:11434/v1"
MODEL = "qwen3.6:latest"


def make_model() -> OpenAIResponsesModel:
    """Create an OpenAIResponsesModel backed by the LlamaStackProvider."""
    provider = LlamaStackProvider(base_url=OLLAMA_URL)
    return OpenAIResponsesModel(MODEL, provider=provider)


# --- Complex nested model ---


class DiagnosticStep(BaseModel):
    command: str
    purpose: str


class TroubleshootingReport(BaseModel):
    title: str
    severity: Literal["low", "medium", "high", "critical"]
    affected_component: str
    root_cause: str
    diagnostic_steps: list[DiagnosticStep]
    resolution: str


async def complex_nested() -> None:
    """Agent returns a complex nested model — validated automatically."""
    agent = Agent(
        make_model(),
        defer_model_check=True,
        output_type=TroubleshootingReport,
        instructions=(
            "Analyze the issue and produce a structured troubleshooting report. "
            "Include 2-3 diagnostic commands with their purpose."
        ),
    )

    result = await agent.run(
        "Our Ansible playbook fails with 'Permission denied' when trying to "
        "install packages on RHEL 9 target hosts."
    )
    report = result.output
    print("=== Complex Nested Model ===")
    print(f"  Title:     {report.title}")
    print(f"  Severity:  {report.severity}")
    print(f"  Component: {report.affected_component}")
    print(f"  Cause:     {report.root_cause}")
    print(f"  Steps:")
    for step in report.diagnostic_steps:
        print(f"    - {step.command}")
        print(f"      ({step.purpose})")
    print(f"  Fix:       {report.resolution}")
    print()


# --- Union output types ---


class Solution(BaseModel):
    kind: Literal["solution"] = "solution"
    answer: str
    confidence: Literal["low", "medium", "high"]


class NeedMoreInfo(BaseModel):
    kind: Literal["need_more_info"] = "need_more_info"
    questions: list[str]


async def union_output() -> None:
    """Agent returns either a Solution or NeedMoreInfo — app branches on result."""
    agent = Agent(
        make_model(),
        defer_model_check=True,
        output_type=Union[Solution, NeedMoreInfo],  # noqa: UP007
        instructions=(
            "If you can confidently answer the question, return a Solution with your answer "
            "and confidence level. If you need more information to answer well, return "
            "NeedMoreInfo with 1-3 clarifying questions. "
            "Prefer NeedMoreInfo when the question is ambiguous."
        ),
    )

    clear_question = "How do I restart a systemd service on RHEL?"
    vague_question = "My app is slow."

    print("=== Union Output Types ===")
    for label, question in [("Clear", clear_question), ("Vague", vague_question)]:
        result = await agent.run(question)
        output = result.output
        print(f"  [{label}] Q: {question}")
        if isinstance(output, Solution):
            print(f"    -> Solution (confidence: {output.confidence})")
            print(f"       {output.answer}")
        elif isinstance(output, NeedMoreInfo):
            print(f"    -> Need more info:")
            for q in output.questions:
                print(f"       - {q}")
        print()


async def main() -> None:
    """Run all structured output examples."""
    await complex_nested()
    await union_output()


if __name__ == "__main__":
    asyncio.run(main())
