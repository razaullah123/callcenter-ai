"""Multi-agent platform: agents, releases, providers, tools and skills stored in the database (Phase 12).

Names are imported lazily: runtime.control.config_store uses platform.bundle, and platform.loader uses
config_store — an eager import here would be circular.
"""

from importlib import import_module

_EXPORTS = {
    "DEFAULT_AGENT": "bundle", "KNOBS": "bundle", "agent_settings": "bundle", "repo_bundle": "bundle",
    "AgentLoader": "loader", "LoadedAgent": "loader", "agent_of": "loader", "mcp_config": "loader",
    "WORKSPACE": "store", "MemoryStore": "store", "PgStore": "store",
    "current_project": "store", "in_project": "store", "set_project": "store",
}


def __getattr__(name: str):
    if name in _EXPORTS:
        return getattr(import_module(f"{__name__}.{_EXPORTS[name]}"), name)
    raise AttributeError(name)


__all__ = sorted(_EXPORTS)
