"""PoC: MCP tool calling with in-process FastMCP servers.

Demonstrates Pydantic AI's native MCP support — no external server process needed.
FastMCP servers run in-process and are connected via FastMCPToolset.

Run: uv run python playground/try_mcp.py
"""

import asyncio

from fastmcp import FastMCP
from pydantic_ai import Agent
from pydantic_ai.mcp import MCPToolset

import sys; sys.path.append(str(__import__("pathlib").Path(__file__).resolve().parent.parent))
from playground.common import make_model


# --- MCP Server: Todo Manager ---

todo_server = FastMCP(name="Todo Manager")
TODOS: list[dict] = []
_todo_id = 0


@todo_server.tool()
def create_todo(title: str, priority: str = "medium") -> dict:
    """Create a new todo item. Priority: low, medium, high."""
    global _todo_id
    _todo_id += 1
    todo = {"id": _todo_id, "title": title, "priority": priority, "status": "open"}
    TODOS.append(todo)
    return todo


@todo_server.tool()
def list_todos() -> list[dict]:
    """List all open todo items."""
    return [t for t in TODOS if t["status"] == "open"]


@todo_server.tool()
def complete_todo(todo_id: int) -> dict:
    """Mark a todo as done by its ID."""
    for t in TODOS:
        if t["id"] == todo_id:
            t["status"] = "done"
            return t
    return {"error": f"Todo {todo_id} not found"}


# --- MCP Server: Calculator ---

calc_server = FastMCP(name="Calculator")


@calc_server.tool()
def add(a: float, b: float) -> float:
    """Add two numbers."""
    return a + b


@calc_server.tool()
def multiply(a: float, b: float) -> float:
    """Multiply two numbers."""
    return a * b


# --- Examples ---


async def single_mcp_server() -> None:
    """Agent with a single MCP server — todo management."""
    TODOS.clear()
    global _todo_id
    _todo_id = 0

    agent = Agent(
        make_model(),
        defer_model_check=True,
        instructions="Use the todo tools to manage tasks. Be concise.",
        toolsets=[MCPToolset(todo_server)],
    )

    async with agent:
        result = await agent.run(
            "Create three todos: 'Deploy v2.0' (high), 'Update docs' (medium), "
            "'Clean up logs' (low). Then list all open todos."
        )
    print("=== Single MCP Server (Todo) ===")
    print(result.output)
    print()


async def multiple_mcp_servers() -> None:
    """Agent with two MCP servers — tools from both are available."""
    TODOS.clear()
    global _todo_id
    _todo_id = 0

    agent = Agent(
        make_model(),
        defer_model_check=True,
        instructions="You have access to a todo manager and a calculator. Use them as needed.",
        toolsets=[
            MCPToolset(todo_server),
            MCPToolset(calc_server),
        ],
    )

    async with agent:
        result = await agent.run(
            "Create a todo 'Budget review' with high priority. "
            "Also, what is 1234 * 5678?"
        )
    print("=== Multiple MCP Servers (Todo + Calculator) ===")
    print(result.output)
    print()


async def main() -> None:
    """Run all MCP examples."""
    await single_mcp_server()
    await multiple_mcp_servers()


if __name__ == "__main__":
    asyncio.run(main())
