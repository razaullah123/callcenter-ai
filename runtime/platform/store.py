"""Platform records: workspaces, providers, MCP servers, tools, skills (+ versions), agents, releases, routes.

`PgStore` is the real store (Postgres, db/schema.sql). `MemoryStore` has the same behaviour in memory — used by
tests and by offline tools that run without a database.
"""

import asyncio
import copy
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

WORKSPACE = "hmg"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class MemoryStore:
    def __init__(self) -> None:
        self.workspaces: dict[str, dict] = {}
        self._providers: dict[str, dict] = {}
        self._mcp: dict[str, dict] = {}
        self._tools: dict[tuple[str, str], dict] = {}
        self._skills: dict[tuple[str, str], dict] = {}
        self._skill_versions: dict[tuple[str, str], list[dict]] = {}
        self._agents: dict[str, dict] = {}
        self._releases: dict[int, dict] = {}
        self._routes: dict[tuple[str, str], dict] = {}
        self._meta: dict[str, Any] = {}
        self._secrets: dict[tuple[str, str], dict] = {}

    @asynccontextmanager
    async def advisory_lock(self, key: str):
        lock = self.__dict__.setdefault("_locks", {}).setdefault(key, asyncio.Lock())
        async with lock:
            yield

    # ---- workspaces
    async def ensure_workspace(self, ws: str, name: str) -> bool:
        if ws in self.workspaces:
            return False
        self.workspaces[ws] = {"id": ws, "name": name, "created_at": _now()}
        return True

    # ---- providers
    async def providers(self, ws: str = WORKSPACE) -> list[dict]:
        return [copy.deepcopy(p) for p in self._providers.values() if p["workspace_id"] == ws]

    async def provider(self, pid: str) -> dict | None:
        return copy.deepcopy(self._providers.get(pid))

    async def put_provider(self, row: dict) -> None:
        self._providers[row["id"]] = copy.deepcopy({**row, "updated_at": _now()})

    async def delete_provider(self, pid: str) -> None:
        self._providers.pop(pid, None)

    # ---- secrets (ciphertext only)
    async def secrets(self, ws: str = WORKSPACE) -> list[dict]:
        return [{k: v for k, v in r.items() if k != "ciphertext"}
                for (w, _), r in sorted(self._secrets.items()) if w == ws]

    async def secret(self, ws: str, name: str) -> dict | None:
        return copy.deepcopy(self._secrets.get((ws, name)))

    async def put_secret(self, ws: str, name: str, ciphertext: str, hint: str, by: str) -> None:
        self._secrets[(ws, name)] = {"workspace_id": ws, "name": name, "ciphertext": ciphertext, "hint": hint,
                                     "updated_by": by, "updated_at": _now()}

    async def delete_secret(self, ws: str, name: str) -> None:
        self._secrets.pop((ws, name), None)

    # ---- MCP servers
    async def mcp_servers(self, ws: str = WORKSPACE) -> list[dict]:
        return [copy.deepcopy(m) for m in self._mcp.values() if m["workspace_id"] == ws]

    async def put_mcp_server(self, row: dict) -> None:
        self._mcp[row["id"]] = copy.deepcopy({"enabled": True, **row, "updated_at": _now()})

    async def delete_mcp_server(self, sid: str) -> None:
        self._mcp.pop(sid, None)

    # ---- tools
    async def tools(self, ws: str = WORKSPACE) -> list[dict]:
        return [copy.deepcopy(t) for (w, _), t in sorted(self._tools.items()) if w == ws]

    async def delete_tool(self, ws: str, name: str) -> None:
        self._tools.pop((ws, name), None)

    async def put_tools(self, ws: str, rows: list[dict]) -> None:
        for r in rows:
            self._tools[(ws, r["name"])] = copy.deepcopy({**r, "workspace_id": ws, "updated_at": _now()})

    # ---- skills
    async def skills(self, ws: str = WORKSPACE) -> list[dict]:
        out = []
        for (w, name), row in sorted(self._skills.items()):
            if w == ws:
                versions = self._skill_versions.get((w, name), [])
                out.append({**row, "latest": versions[-1]["version"] if versions else None})
        return out

    async def skill_version(self, ws: str, name: str, version: int) -> dict[str, str] | None:
        for v in self._skill_versions.get((ws, name), []):
            if v["version"] == version:
                return copy.deepcopy(v["files"])
        return None

    async def skill_versions(self, ws: str, name: str) -> list[dict]:
        return [{k: v[k] for k in ("version", "created_at", "author", "note")}
                for v in reversed(self._skill_versions.get((ws, name), []))]

    async def add_skill_version(self, ws: str, name: str, files: dict[str, str], author: str, note: str,
                                repo_hash: str | None = None) -> int:
        versions = self._skill_versions.setdefault((ws, name), [])
        version = (versions[-1]["version"] if versions else 0) + 1
        versions.append({"version": version, "files": copy.deepcopy(files), "author": author, "note": note,
                         "created_at": _now()})
        row = self._skills.setdefault((ws, name), {"workspace_id": ws, "name": name, "repo_hash": None})
        if repo_hash is not None:
            row["repo_hash"] = repo_hash
        row["updated_at"] = _now()
        return version

    async def set_skill_repo_hash(self, ws: str, name: str, repo_hash: str) -> None:
        row = self._skills.setdefault((ws, name), {"workspace_id": ws, "name": name, "repo_hash": None})
        row["repo_hash"] = repo_hash

    # ---- agents + releases
    async def agents(self, ws: str = WORKSPACE) -> list[dict]:
        return [copy.deepcopy(a) for a in self._agents.values() if a["workspace_id"] == ws]

    async def agent(self, agent_id: str) -> dict | None:
        return copy.deepcopy(self._agents.get(agent_id))

    async def put_agent(self, row: dict) -> None:
        old = self._agents.get(row["id"], {})
        self._agents[row["id"]] = {"description": "", "published_release_id": None, **old, **copy.deepcopy(row),
                                   "updated_at": _now()}

    async def set_draft(self, agent_id: str, bundle: dict | None, by: str | None = None) -> None:
        a = self._agents[agent_id]
        a["draft"], a["draft_updated_by"], a["draft_updated_at"] = copy.deepcopy(bundle), by, _now() if bundle else None

    async def delete_agent(self, agent_id: str) -> None:
        self._agents.pop(agent_id, None)
        for rid in [r for r, rel in self._releases.items() if rel["agent_id"] == agent_id]:
            self._releases.pop(rid)

    async def delete_route(self, ws: str, pattern: str) -> None:
        self._routes.pop((ws, pattern), None)

    async def release(self, release_id: int) -> dict | None:
        return copy.deepcopy(self._releases.get(release_id))

    async def releases(self, agent_id: str, limit: int = 30) -> list[dict]:
        rows = sorted((r for r in self._releases.values() if r["agent_id"] == agent_id), key=lambda r: -r["version"])
        return [{k: r[k] for k in ("id", "version", "author", "note", "created_at")} for r in rows[:limit]]

    async def add_release(self, agent_id: str, bundle: dict, author: str, note: str, *, publish: bool = True) -> dict:
        version = 1 + max((r["version"] for r in self._releases.values() if r["agent_id"] == agent_id), default=0)
        rid = 1 + max(self._releases, default=0)
        self._releases[rid] = {"id": rid, "agent_id": agent_id, "version": version, "bundle": copy.deepcopy(bundle),
                               "author": author, "note": note, "created_at": _now()}
        if publish:
            await self.publish(agent_id, rid)
        return {"id": rid, "version": version}

    async def publish(self, agent_id: str, release_id: int) -> None:
        self._agents[agent_id]["published_release_id"] = release_id
        self._agents[agent_id]["updated_at"] = _now()

    # ---- routes + meta
    async def routes(self, ws: str = WORKSPACE) -> list[dict]:
        return sorted((copy.deepcopy(r) for (w, _), r in self._routes.items() if w == ws),
                      key=lambda r: -r.get("priority", 0))

    async def put_route(self, ws: str, pattern: str, agent_id: str, priority: int = 0) -> None:
        self._routes[(ws, pattern)] = {"workspace_id": ws, "pattern": pattern, "agent_id": agent_id,
                                       "priority": priority}

    async def meta_get(self, key: str) -> Any:
        return copy.deepcopy(self._meta.get(key))

    async def meta_set(self, key: str, value: Any) -> None:
        self._meta[key] = copy.deepcopy(value)


def _j(v: Any) -> Any:
    return v if not isinstance(v, str) else json.loads(v)


class PgStore:
    """The same interface on Postgres (tables in db/schema.sql)."""

    def __init__(self, pool=None) -> None:
        self._pool = pool

    async def _p(self):
        if self._pool is None:
            from runtime.data.db import get_pool
            self._pool = await get_pool()
        return self._pool

    async def _fetch(self, sql: str, *args) -> list[dict]:
        async with (await self._p()).acquire() as c:
            return [dict(r) for r in await c.fetch(sql, *args)]

    async def _row(self, sql: str, *args) -> dict | None:
        rows = await self._fetch(sql, *args)
        return rows[0] if rows else None

    async def _exec(self, sql: str, *args) -> str:
        async with (await self._p()).acquire() as c:
            return await c.execute(sql, *args)

    @asynccontextmanager
    async def advisory_lock(self, key: str):
        """Cluster-wide mutex (Postgres advisory lock) — e.g. seeding when several workers start at once."""
        async with (await self._p()).acquire() as c:
            await c.execute("SELECT pg_advisory_lock(hashtext($1))", key)
            try:
                yield
            finally:
                await c.execute("SELECT pg_advisory_unlock(hashtext($1))", key)

    # ---- workspaces
    async def ensure_workspace(self, ws: str, name: str) -> bool:
        r = await self._exec("INSERT INTO workspaces (id, name) VALUES ($1, $2) ON CONFLICT (id) DO NOTHING", ws, name)
        return r.endswith("1")

    # ---- providers
    @staticmethod
    def _provider(r: dict) -> dict:
        return {**r, "settings": _j(r["settings"])}

    async def providers(self, ws: str = WORKSPACE) -> list[dict]:
        return [self._provider(r) for r in
                await self._fetch("SELECT * FROM providers WHERE workspace_id = $1 ORDER BY kind, name", ws)]

    async def provider(self, pid: str) -> dict | None:
        r = await self._row("SELECT * FROM providers WHERE id = $1", pid)
        return self._provider(r) if r else None

    async def put_provider(self, row: dict) -> None:
        await self._exec(
            """INSERT INTO providers (id, workspace_id, kind, name, type, settings, updated_by)
               VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7)
               ON CONFLICT (id) DO UPDATE SET kind = EXCLUDED.kind, name = EXCLUDED.name, type = EXCLUDED.type,
                   settings = EXCLUDED.settings, updated_by = EXCLUDED.updated_by, updated_at = now()""",
            row["id"], row["workspace_id"], row["kind"], row["name"], row["type"],
            json.dumps(row.get("settings") or {}), row.get("updated_by"))

    async def delete_provider(self, pid: str) -> None:
        await self._exec("DELETE FROM providers WHERE id = $1", pid)

    # ---- secrets (ciphertext only)
    async def secrets(self, ws: str = WORKSPACE) -> list[dict]:
        return await self._fetch("SELECT workspace_id, name, hint, updated_by, updated_at FROM secrets "
                                 "WHERE workspace_id = $1 ORDER BY name", ws)

    async def secret(self, ws: str, name: str) -> dict | None:
        return await self._row("SELECT * FROM secrets WHERE workspace_id = $1 AND name = $2", ws, name)

    async def put_secret(self, ws: str, name: str, ciphertext: str, hint: str, by: str) -> None:
        await self._exec("INSERT INTO secrets (workspace_id, name, ciphertext, hint, updated_by) "
                         "VALUES ($1, $2, $3, $4, $5) ON CONFLICT (workspace_id, name) DO UPDATE SET "
                         "ciphertext = EXCLUDED.ciphertext, hint = EXCLUDED.hint, updated_by = EXCLUDED.updated_by, "
                         "updated_at = now()", ws, name, ciphertext, hint, by)

    async def delete_secret(self, ws: str, name: str) -> None:
        await self._exec("DELETE FROM secrets WHERE workspace_id = $1 AND name = $2", ws, name)

    # ---- MCP servers
    async def mcp_servers(self, ws: str = WORKSPACE) -> list[dict]:
        return [{**r, "auth": _j(r["auth"])} for r in
                await self._fetch("SELECT * FROM mcp_servers WHERE workspace_id = $1 ORDER BY name", ws)]

    async def put_mcp_server(self, row: dict) -> None:
        await self._exec(
            """INSERT INTO mcp_servers (id, workspace_id, name, transport, url, auth, enabled)
               VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7)
               ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, transport = EXCLUDED.transport,
                   url = EXCLUDED.url, auth = EXCLUDED.auth, enabled = EXCLUDED.enabled, updated_at = now()""",
            row["id"], row["workspace_id"], row["name"], row.get("transport", "streamable_http"), row["url"],
            json.dumps(row.get("auth") or {}), row.get("enabled", True))

    async def delete_mcp_server(self, sid: str) -> None:
        await self._exec("DELETE FROM mcp_servers WHERE id = $1", sid)

    # ---- tools
    async def delete_tool(self, ws: str, name: str) -> None:
        await self._exec("DELETE FROM tools WHERE workspace_id = $1 AND name = $2", ws, name)

    async def tools(self, ws: str = WORKSPACE) -> list[dict]:
        return [{**r, "policy": _j(r["policy"])} for r in
                await self._fetch("SELECT * FROM tools WHERE workspace_id = $1 ORDER BY grp, name", ws)]

    async def put_tools(self, ws: str, rows: list[dict]) -> None:
        async with (await self._p()).acquire() as c, c.transaction():
            for r in rows:
                await c.execute(
                    """INSERT INTO tools (workspace_id, name, grp, source, policy) VALUES ($1, $2, $3, $4, $5::jsonb)
                       ON CONFLICT (workspace_id, name) DO UPDATE SET grp = EXCLUDED.grp, source = EXCLUDED.source,
                           policy = EXCLUDED.policy, updated_at = now()""",
                    ws, r["name"], r["grp"], r.get("source", "mcp"), json.dumps(r.get("policy") or {}))

    # ---- skills
    async def skills(self, ws: str = WORKSPACE) -> list[dict]:
        return await self._fetch(
            """SELECT s.*, (SELECT max(version) FROM skill_versions v WHERE v.workspace_id = s.workspace_id
                              AND v.skill = s.name) AS latest
               FROM skills s WHERE s.workspace_id = $1 ORDER BY s.name""", ws)

    async def skill_version(self, ws: str, name: str, version: int) -> dict[str, str] | None:
        r = await self._row("SELECT files FROM skill_versions WHERE workspace_id = $1 AND skill = $2 AND version = $3",
                            ws, name, version)
        return _j(r["files"]) if r else None

    async def skill_versions(self, ws: str, name: str) -> list[dict]:
        return await self._fetch("SELECT version, created_at, author, note FROM skill_versions "
                                 "WHERE workspace_id = $1 AND skill = $2 ORDER BY version DESC", ws, name)

    async def add_skill_version(self, ws: str, name: str, files: dict[str, str], author: str, note: str,
                                repo_hash: str | None = None) -> int:
        async with (await self._p()).acquire() as c, c.transaction():
            await c.execute("INSERT INTO skills (workspace_id, name) VALUES ($1, $2) ON CONFLICT DO NOTHING", ws, name)
            await c.execute("SELECT 1 FROM skills WHERE workspace_id = $1 AND name = $2 FOR UPDATE", ws, name)
            version = 1 + await c.fetchval("SELECT coalesce(max(version), 0) FROM skill_versions "
                                           "WHERE workspace_id = $1 AND skill = $2", ws, name)
            await c.execute("INSERT INTO skill_versions (workspace_id, skill, version, author, note, files) "
                            "VALUES ($1, $2, $3, $4, $5, $6::jsonb)", ws, name, version, author, note,
                            json.dumps(files))
            await c.execute("UPDATE skills SET updated_at = now(), repo_hash = coalesce($3, repo_hash) "
                            "WHERE workspace_id = $1 AND name = $2", ws, name, repo_hash)
        return version

    async def set_skill_repo_hash(self, ws: str, name: str, repo_hash: str) -> None:
        await self._exec("INSERT INTO skills (workspace_id, name, repo_hash) VALUES ($1, $2, $3) "
                         "ON CONFLICT (workspace_id, name) DO UPDATE SET repo_hash = EXCLUDED.repo_hash", ws, name,
                         repo_hash)

    async def legacy_agent_config(self) -> dict | None:
        """The active Phase-9 provider config (agent_config), read once when seeding."""
        try:
            r = await self._row("SELECT config FROM agent_config WHERE active ORDER BY version DESC LIMIT 1")
        except Exception:
            return None
        return _j(r["config"]) if r else None

    # ---- agents + releases
    async def agents(self, ws: str = WORKSPACE) -> list[dict]:
        rows = await self._fetch("SELECT * FROM agents WHERE workspace_id = $1 ORDER BY name", ws)
        return [{**r, "draft": _j(r["draft"]) if r.get("draft") is not None else None} for r in rows]

    async def agent(self, agent_id: str) -> dict | None:
        r = await self._row("SELECT * FROM agents WHERE id = $1", agent_id)
        if r and r.get("draft") is not None:
            r["draft"] = _j(r["draft"])
        return r

    async def put_agent(self, row: dict) -> None:
        await self._exec(
            """INSERT INTO agents (id, workspace_id, name, description) VALUES ($1, $2, $3, $4)
               ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, description = EXCLUDED.description,
                   updated_at = now()""",
            row["id"], row["workspace_id"], row["name"], row.get("description", ""))

    async def set_draft(self, agent_id: str, bundle: dict | None, by: str | None = None) -> None:
        await self._exec("UPDATE agents SET draft = $2::jsonb, draft_updated_by = $3, "
                         "draft_updated_at = CASE WHEN $2::jsonb IS NULL THEN NULL ELSE now() END WHERE id = $1",
                         agent_id, json.dumps(bundle, default=str) if bundle is not None else None, by)

    async def delete_agent(self, agent_id: str) -> None:
        async with (await self._p()).acquire() as c, c.transaction():
            await c.execute("DELETE FROM agent_releases WHERE agent_id = $1", agent_id)
            await c.execute("DELETE FROM agents WHERE id = $1", agent_id)

    async def delete_route(self, ws: str, pattern: str) -> None:
        await self._exec("DELETE FROM phone_routes WHERE workspace_id = $1 AND pattern = $2", ws, pattern)

    async def release(self, release_id: int) -> dict | None:
        r = await self._row("SELECT * FROM agent_releases WHERE id = $1", release_id)
        return {**r, "bundle": _j(r["bundle"])} if r else None

    async def releases(self, agent_id: str, limit: int = 30) -> list[dict]:
        return await self._fetch("SELECT id, version, author, note, created_at FROM agent_releases "
                                 "WHERE agent_id = $1 ORDER BY version DESC LIMIT $2", agent_id, limit)

    async def add_release(self, agent_id: str, bundle: dict, author: str, note: str, *, publish: bool = True) -> dict:
        async with (await self._p()).acquire() as c, c.transaction():
            await c.execute("SELECT 1 FROM agents WHERE id = $1 FOR UPDATE", agent_id)
            version = 1 + await c.fetchval("SELECT coalesce(max(version), 0) FROM agent_releases WHERE agent_id = $1",
                                           agent_id)
            rid = await c.fetchval("INSERT INTO agent_releases (agent_id, version, bundle, author, note) "
                                   "VALUES ($1, $2, $3::jsonb, $4, $5) RETURNING id",
                                   agent_id, version, json.dumps(bundle, default=str), author, note)
            if publish:
                await c.execute("UPDATE agents SET published_release_id = $2, updated_at = now() WHERE id = $1",
                                agent_id, rid)
        return {"id": rid, "version": version}

    async def publish(self, agent_id: str, release_id: int) -> None:
        await self._exec("UPDATE agents SET published_release_id = $2, updated_at = now() WHERE id = $1",
                         agent_id, release_id)

    # ---- routes + meta
    async def routes(self, ws: str = WORKSPACE) -> list[dict]:
        return await self._fetch("SELECT * FROM phone_routes WHERE workspace_id = $1 ORDER BY priority DESC", ws)

    async def put_route(self, ws: str, pattern: str, agent_id: str, priority: int = 0) -> None:
        await self._exec("INSERT INTO phone_routes (workspace_id, pattern, agent_id, priority) VALUES ($1, $2, $3, $4) "
                         "ON CONFLICT (workspace_id, pattern) DO UPDATE SET agent_id = EXCLUDED.agent_id, "
                         "priority = EXCLUDED.priority", ws, pattern, agent_id, priority)

    async def meta_get(self, key: str) -> Any:
        r = await self._row("SELECT value FROM platform_meta WHERE key = $1", key)
        return _j(r["value"]) if r else None

    async def meta_set(self, key: str, value: Any) -> None:
        await self._exec("INSERT INTO platform_meta (key, value) VALUES ($1, $2::jsonb) "
                         "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", key, json.dumps(value, default=str))
