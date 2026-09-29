"""Local (in-process) tools. Registered with a pydantic model for arguments; the model's JSON
schema is what the LLM sees, exactly like an MCP tool's inputSchema.

    class Args(BaseModel):
        query: str = Field(description="...")

    @local_tool("find_hospital_by_name", "Find a hospital by the name the caller said", Args)
    async def find_hospital_by_name(args: Args, ctx: ToolContext) -> dict: ...
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from .types import ToolContext

LocalFn = Callable[[Any, ToolContext], Awaitable[Any]]


@dataclass(frozen=True)
class LocalTool:
    name: str
    description: str
    args_model: type[BaseModel]
    fn: LocalFn


_LOCAL: dict[str, LocalTool] = {}


def local_tool(name: str, description: str, args_model: type[BaseModel]):
    def deco(fn: LocalFn) -> LocalFn:
        _LOCAL[name] = LocalTool(name, description, args_model, fn)
        return fn
    return deco


def local_tools() -> dict[str, LocalTool]:
    return dict(_LOCAL)


def local_schemas() -> dict[str, dict[str, Any]]:
    out = {}
    for t in _LOCAL.values():
        schema = t.args_model.model_json_schema()
        schema.pop("title", None)
        for prop in schema.get("properties", {}).values():
            prop.pop("title", None)
        out[t.name] = {"description": t.description, "input_schema": schema}
    return out


async def call_local(name: str, arguments: dict[str, Any], ctx: ToolContext) -> Any:
    tool = _LOCAL[name]
    return await tool.fn(tool.args_model.model_validate(arguments), ctx)
