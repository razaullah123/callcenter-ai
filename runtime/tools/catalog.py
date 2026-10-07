"""Tool catalog: config/tools.yaml (policy) + tool input schemas (from MCP / local registry)."""

import json
from pathlib import Path
from typing import Any

import yaml

from runtime.config import ROOT_DIR

from .types import ToolDef

CATALOG_PATH = ROOT_DIR / "config" / "tools.yaml"
SNAPSHOT_PATH = ROOT_DIR / "mcp_tools.json"

DEFAULT_TIMEOUT = {"read": 8.0, "write": 15.0, "send": 10.0}


class Catalog:
    def __init__(self, tools: dict[str, ToolDef], inject: dict[str, list[str]], server: str) -> None:
        self.tools = tools
        self.inject = inject          # session field → parameter-name aliases
        self.server = server

    def __contains__(self, name: str) -> bool:
        return name in self.tools

    def get(self, name: str) -> ToolDef | None:
        return self.tools.get(name)

    def for_skill(self, skill: str) -> list[ToolDef]:
        return [t for t in self.tools.values() if t.skill == skill]

    def by_role(self, role: str) -> ToolDef | None:
        """The tool that plays a harness role (e.g. "identity.verify_code"), if this agent has one."""
        return next((t for t in self.tools.values() if t.role == role), None)

    def injected_param(self, param: str) -> str | None:
        """Session field that fills `param`, if it is an injected parameter."""
        for session_field, aliases in self.inject.items():
            if param in aliases:
                return session_field
        return None

    @classmethod
    def load(cls, mcp_schemas: dict[str, dict[str, Any]], local_schemas: dict[str, dict[str, Any]],
             path: Path = CATALOG_PATH) -> "Catalog":
        """`*_schemas`: tool name → {"description", "input_schema"}."""
        return cls.from_config(load_tools_config(path), mcp_schemas, local_schemas)

    @classmethod
    def from_config(cls, cfg: dict[str, Any], mcp_schemas: dict[str, dict[str, Any]],
                    local_schemas: dict[str, dict[str, Any]]) -> "Catalog":
        """`cfg`: the tools.yaml shape — {server, inject, skills: {group: {tool: policy}}} (an agent release keeps
        its own frozen copy)."""
        tools: dict[str, ToolDef] = {}
        missing: list[str] = []
        for skill, entries in cfg["skills"].items():
            for name, policy in entries.items():
                source = policy.get("source", "mcp")
                if source in ("http", "web"):  # API-request / web tool: its schema is part of its own definition
                    schema = {"description": policy.get("description", ""),
                              "input_schema": policy.get("input_schema") or {"type": "object", "properties": {}}}
                else:
                    schema = (local_schemas if source == "local" else mcp_schemas).get(name)
                if schema is None:
                    missing.append(name)
                    continue
                kind = policy.get("kind", "read")
                tools[name] = ToolDef(
                    name=name, skill=skill, kind=kind, source=source,
                    description=schema.get("description") or "",
                    input_schema=schema.get("input_schema") or {"type": "object", "properties": {}},
                    cache_ttl=float(policy.get("cache_ttl", 0)),
                    idempotent=bool(policy.get("idempotent", False)),
                    timeout_s=policy.get("timeout_s", DEFAULT_TIMEOUT[kind]),
                    confirm=policy.get("confirm", "none" if kind == "read" else "affirm"),
                    role=policy.get("role"), role_args=dict(policy.get("args") or {}),
                    hooks=tuple(policy.get("hooks") or ()), backs=policy.get("backs"),
                    success_line=policy.get("success_line"), http=policy.get("http"),
                    enabled=policy.get("enabled", True) is not False, run_async=bool(policy.get("async", False)),
                    say_start=policy.get("say_start") or None, say_done=policy.get("say_done") or None,
                )
        if missing:
            raise ValueError(f"tools in catalog but not provided by any server: {missing}")
        return cls(tools, cfg.get("inject", {}), cfg.get("server", "hmg_tools"))


def load_tools_config(path: Path = CATALOG_PATH) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def snapshot_schemas(path: Path = SNAPSHOT_PATH, server: str = "hmg_tools") -> dict[str, dict[str, Any]]:
    """Tool schemas from the discovery snapshot (offline dev, mock server, tests)."""
    data = json.loads(path.read_text(encoding="utf-8"))[server]
    return {t["name"]: {"description": t.get("description", ""), "input_schema": t.get("inputSchema", {})}
            for t in data["tools"]}
