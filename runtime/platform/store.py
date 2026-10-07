"""Platform records: workspaces, providers, MCP servers, tools, skills (+ versions), agents, releases, routes.

`PgStore` is the real store (Postgres, db/schema.sql). `MemoryStore` has the same behaviour in memory — used by
tests and by offline tools that run without a database.
"""

import asyncio
import copy
import json
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

WORKSPACE = "hmg"                   # the default project (HMG); others are created from the console

# The project a console request / an agent build works in (projects = workspaces table). Set per console request
# from the X-Project header (control/api.py) and by the loader while it builds an agent of another project.
_PROJECT: ContextVar[str] = ContextVar("project", default=WORKSPACE)


def current_project() -> str:
    return _PROJECT.get()


@contextmanager
def in_project(ws: str):
    token = _PROJECT.set(ws or WORKSPACE)
    try:
        yield
    finally:
        _PROJECT.reset(token)


def set_project(ws: str) -> None:
    """For a request handler: the rest of this request works in `ws`."""
    _PROJECT.set(ws)


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
        self._eval_cases: dict[tuple[str, str], dict] = {}
        self._audit: list[dict] = []
        self._shares: dict[str, dict] = {}
        self._outbound: dict[tuple[str, str], dict] = {}
        self._batches: dict[str, dict] = {}
        self._deliveries: list[dict] = []
        self._analysis: dict[str, dict] = {}
        self._recordings: dict[str, dict] = {}
        self._api_keys: dict[str, dict] = {}
        self._recipients: dict[int, dict] = {}

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

    async def list_workspaces(self) -> list[dict]:
        return sorted((copy.deepcopy(w) for w in self.workspaces.values()), key=lambda w: w["created_at"])

    async def rename_workspace(self, ws: str, name: str) -> None:
        self.workspaces[ws]["name"] = name

    async def all_routes(self) -> list[dict]:
        return [copy.deepcopy(r) for r in self._routes.values()]

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
        self._agents[row["id"]] = {"description": "", "published_release_id": None, "created_at": _now(), **old,
                                   **copy.deepcopy(row), "updated_at": _now()}

    async def set_draft(self, agent_id: str, bundle: dict | None, by: str | None = None) -> None:
        a = self._agents[agent_id]
        a["draft"], a["draft_updated_by"], a["draft_updated_at"] = copy.deepcopy(bundle), by, _now() if bundle else None

    async def set_gate(self, agent_id: str, gate: dict | None) -> None:
        self._agents[agent_id]["gate"] = copy.deepcopy(gate)

    async def delete_agent(self, agent_id: str) -> None:
        self._agents.pop(agent_id, None)
        for rid in [r for r, rel in self._releases.items() if rel["agent_id"] == agent_id]:
            self._releases.pop(rid)
        for key in [k for k in self._eval_cases if k[0] == agent_id]:
            self._eval_cases.pop(key)
        self._shares.pop(agent_id, None)

    async def delete_route(self, ws: str, pattern: str) -> None:
        self._routes.pop((ws, pattern), None)

    async def release(self, release_id: int) -> dict | None:
        return copy.deepcopy(self._releases.get(release_id))

    async def releases(self, agent_id: str, limit: int = 30) -> list[dict]:
        rows = sorted((r for r in self._releases.values() if r["agent_id"] == agent_id), key=lambda r: -r["version"])
        return [{k: r.get(k) for k in ("id", "version", "author", "note", "created_at", "gate")} for r in rows[:limit]]

    async def add_release(self, agent_id: str, bundle: dict, author: str, note: str, *, publish: bool = True,
                          gate: dict | None = None) -> dict:
        version = 1 + max((r["version"] for r in self._releases.values() if r["agent_id"] == agent_id), default=0)
        rid = 1 + max(self._releases, default=0)
        self._releases[rid] = {"id": rid, "agent_id": agent_id, "version": version, "bundle": copy.deepcopy(bundle),
                               "author": author, "note": note, "created_at": _now(), "gate": copy.deepcopy(gate)}
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

    async def put_route(self, ws: str, pattern: str, agent_id: str, priority: int = 0, label: str | None = None) -> None:
        old = self._routes.get((ws, pattern)) or {}
        self._routes[(ws, pattern)] = {"workspace_id": ws, "pattern": pattern, "agent_id": agent_id, "priority": priority,
                                       "label": (label or None) if label is not None else old.get("label"),
                                       "created_at": old.get("created_at") or _now()}

    # ---- users, sessions, members, invitations (console sign-in)
    def _acc(self) -> dict:
        return self.__dict__.setdefault("_accounts", {"users": {}, "sessions": {}, "members": {}, "invites": {}})

    async def count_users(self) -> int:
        return len(self._acc()["users"])

    async def create_user(self, row: dict) -> None:
        self._acc()["users"][row["id"]] = {"default_project": None, "last_login_at": None, "created_at": _now(),
                                           **copy.deepcopy(row)}

    async def user(self, user_id: str) -> dict | None:
        return copy.deepcopy(self._acc()["users"].get(user_id))

    async def user_by_email(self, email: str) -> dict | None:
        return copy.deepcopy(next((u for u in self._acc()["users"].values() if u["email"] == email.lower()), None))

    async def update_user(self, user_id: str, **fields) -> None:
        self._acc()["users"][user_id].update(fields)

    async def create_session(self, token_hash: str, user_id: str, expires_at) -> None:
        self._acc()["sessions"][token_hash] = {"user_id": user_id, "expires_at": expires_at}

    async def session_user(self, token_hash: str) -> dict | None:
        s = self._acc()["sessions"].get(token_hash)
        if s is None or s["expires_at"] < _now():
            return None
        return await self.user(s["user_id"])

    async def delete_session(self, token_hash: str) -> None:
        self._acc()["sessions"].pop(token_hash, None)

    async def add_member(self, ws: str, user_id: str, role: str) -> None:
        old = self._acc()["members"].get((ws, user_id), {})
        self._acc()["members"][(ws, user_id)] = {"workspace_id": ws, "user_id": user_id, "role": role,
                                                 "label": old.get("label"), "joined_at": old.get("joined_at", _now())}

    async def members(self, ws: str) -> list[dict]:
        users = self._acc()["users"]
        return [{**copy.deepcopy(m), "email": users[m["user_id"]]["email"], "name": users[m["user_id"]]["name"]}
                for (w, _), m in self._acc()["members"].items() if w == ws and m["user_id"] in users]

    async def memberships(self, user_id: str) -> list[dict]:
        return [copy.deepcopy(m) for (_, u), m in self._acc()["members"].items() if u == user_id]

    async def remove_member(self, ws: str, user_id: str) -> None:
        self._acc()["members"].pop((ws, user_id), None)

    async def set_member_label(self, ws: str, user_id: str, label: str | None) -> None:
        self._acc()["members"][(ws, user_id)]["label"] = label

    async def create_invitation(self, row: dict) -> None:
        self._acc()["invites"][row["id"]] = {"accepted_at": None, "created_at": _now(), **copy.deepcopy(row)}

    async def invitations(self, ws: str) -> list[dict]:
        return [copy.deepcopy(i) for i in self._acc()["invites"].values() if i["workspace_id"] == ws]

    async def invitation_by_token(self, token_hash: str) -> dict | None:
        return copy.deepcopy(next((i for i in self._acc()["invites"].values() if i["token_hash"] == token_hash), None))

    async def update_invitation(self, inv_id: str, **fields) -> None:
        self._acc()["invites"][inv_id].update(fields)

    async def delete_invitation(self, inv_id: str) -> None:
        self._acc()["invites"].pop(inv_id, None)

    # ---- public page / embed
    async def share(self, agent_id: str) -> dict | None:
        return copy.deepcopy(self._shares.get(agent_id))

    async def share_by_token(self, token: str) -> dict | None:
        return copy.deepcopy(next((r for r in self._shares.values() if r["token"] == token), None))

    async def put_share(self, row: dict) -> None:
        old = self._shares.get(row["agent_id"])
        self._shares[row["agent_id"]] = {**(old or {"created_at": _now()}), **copy.deepcopy(row), "updated_at": _now()}

    async def delete_share(self, agent_id: str) -> None:
        self._shares.pop(agent_id, None)

    # ---- API keys
    async def create_api_key(self, row: dict) -> None:
        self._api_keys[row["id"]] = {"created_at": _now(), "last_used_at": None, "expires_at": None, "revoked_at": None,
                                     **copy.deepcopy(row)}

    async def api_key_by_hash(self, key_hash: str) -> dict | None:
        return copy.deepcopy(next((k for k in self._api_keys.values() if k["key_hash"] == key_hash), None))

    async def api_keys(self, ws: str) -> list[dict]:
        return sorted((copy.deepcopy(k) for k in self._api_keys.values() if k["workspace_id"] == ws),
                      key=lambda k: k["created_at"], reverse=True)

    async def revoke_api_key(self, ws: str, key_id: str) -> bool:
        k = self._api_keys.get(key_id)
        if not k or k["workspace_id"] != ws or k["revoked_at"]:
            return False
        k["revoked_at"] = _now()
        return True

    async def touch_api_key(self, key_id: str) -> None:
        if key_id in self._api_keys:
            self._api_keys[key_id]["last_used_at"] = _now()

    # ---- post-call analysis
    async def put_call_analysis(self, row: dict) -> None:
        self._analysis[row["call_id"]] = {**copy.deepcopy(row), "created_at": _now()}

    async def call_analysis(self, call_id: str) -> dict | None:
        return copy.deepcopy(self._analysis.get(call_id))

    # ---- call recordings (metadata; the audio is a file)
    async def put_recording(self, row: dict) -> None:
        self._recordings[row["call_id"]] = {**copy.deepcopy(row), "created_at": _now()}

    async def recording(self, call_id: str) -> dict | None:
        return copy.deepcopy(self._recordings.get(call_id))

    async def due_recordings(self, now=None) -> list[dict]:
        now = now or _now()
        return [copy.deepcopy(r) for r in self._recordings.values() if r["status"] == "ok" and r["expires_at"] <= now]

    async def end_recording(self, call_id: str, status: str) -> None:
        if call_id in self._recordings:
            self._recordings[call_id].update(status=status, path=None)

    async def call_analyses(self, ws: str, start=None, end=None, agent_id: str | None = None) -> list[dict]:
        return [copy.deepcopy(r) for r in self._analysis.values() if r["workspace_id"] == ws
                and (agent_id is None or r.get("agent_id") == agent_id)
                and (start is None or (r.get("started_at") and r["started_at"] >= start))
                and (end is None or (r.get("started_at") and r["started_at"] < end))]

    # ---- webhook delivery history
    async def add_webhook_delivery(self, row: dict) -> None:
        self._deliveries.append({"id": max((d["id"] for d in self._deliveries), default=0) + 1, "created_at": _now(),
                                 **copy.deepcopy(row)})
        mine = [d for d in self._deliveries if d["agent_id"] == row["agent_id"]]
        for old in mine[:-200]:
            self._deliveries.remove(old)

    async def webhook_deliveries(self, agent_id: str, limit: int = 50) -> list[dict]:
        return copy.deepcopy([d for d in reversed(self._deliveries) if d["agent_id"] == agent_id][:limit])

    # ---- batch (outbound) calls
    async def outbound_numbers(self, ws: str) -> list[dict]:
        return sorted((copy.deepcopy(r) for (w, _), r in self._outbound.items() if w == ws), key=lambda r: r["number"])

    async def put_outbound_number(self, row: dict) -> None:
        old = self._outbound.get((row["workspace_id"], row["number"]))
        self._outbound[(row["workspace_id"], row["number"])] = {**(old or {"created_at": _now()}), **copy.deepcopy(row)}

    async def delete_outbound_number(self, ws: str, number: str) -> None:
        self._outbound.pop((ws, number), None)

    async def put_batch(self, row: dict) -> None:
        old = self._batches.get(row["id"])
        self._batches[row["id"]] = {**(old or {"created_at": _now(), "started_at": None, "finished_at": None,
                                               "status": "scheduled", "config": {}}), **copy.deepcopy(row), "updated_at": _now()}

    async def batch(self, batch_id: str) -> dict | None:
        return copy.deepcopy(self._batches.get(batch_id))

    async def batches(self, ws: str) -> list[dict]:
        return sorted((copy.deepcopy(b) for b in self._batches.values() if b["workspace_id"] == ws),
                      key=lambda b: b["created_at"], reverse=True)

    async def batches_in(self, statuses: list[str]) -> list[dict]:
        return [copy.deepcopy(b) for b in self._batches.values() if b["status"] in statuses]

    async def delete_batch(self, batch_id: str) -> None:
        self._batches.pop(batch_id, None)
        self._recipients = {k: r for k, r in self._recipients.items() if r["batch_id"] != batch_id}

    async def add_recipients(self, batch_id: str, rows: list[dict]) -> int:
        for r in rows:
            rid = max(self._recipients, default=0) + 1
            self._recipients[rid] = {"id": rid, "batch_id": batch_id, "phone": r["phone"], "name": r.get("name"),
                                     "variables": copy.deepcopy(r.get("variables") or {}), "status": "pending",
                                     "call_id": None, "error": None, "attempts": 0, "dialed_at": None,
                                     "ended_at": None, "duration_s": None}
        return len(rows)

    async def recipients(self, batch_id: str, status: str | None = None, q: str | None = None,
                         limit: int = 100, offset: int = 0) -> tuple[list[dict], int]:
        rows = sorted((r for r in self._recipients.values() if r["batch_id"] == batch_id), key=lambda r: r["id"])
        if status:
            rows = [r for r in rows if r["status"] == status]
        if q:
            q = q.lower()
            rows = [r for r in rows if q in r["phone"].lower() or q in (r["name"] or "").lower()]
        return copy.deepcopy(rows[offset:offset + limit]), len(rows)

    async def recipient(self, rid: int) -> dict | None:
        return copy.deepcopy(self._recipients.get(rid))

    async def recipient_counts(self, batch_id: str) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in self._recipients.values():
            if r["batch_id"] == batch_id:
                out[r["status"]] = out.get(r["status"], 0) + 1
        return out

    async def update_recipient(self, rid: int, patch: dict) -> None:
        if rid in self._recipients:
            self._recipients[rid].update(copy.deepcopy(patch))

    async def pending_recipients(self, batch_id: str, limit: int) -> list[dict]:
        rows = sorted((r for r in self._recipients.values() if r["batch_id"] == batch_id and r["status"] == "pending"),
                      key=lambda r: r["id"])
        return copy.deepcopy(rows[:limit])

    async def recipients_with_status(self, statuses: list[str]) -> list[dict]:
        return [copy.deepcopy(r) for r in self._recipients.values() if r["status"] in statuses]

    async def reset_recipients(self, batch_id: str, statuses: list[str]) -> int:
        n = 0
        for r in self._recipients.values():
            if r["batch_id"] == batch_id and r["status"] in statuses:
                r.update(status="pending", error=None, call_id=None, dialed_at=None, ended_at=None, duration_s=None)
                n += 1
        return n

    async def delete_recipient(self, batch_id: str, rid: int) -> None:
        if (self._recipients.get(rid) or {}).get("batch_id") == batch_id:
            self._recipients.pop(rid)

    # ---- test cases + audit (publish gate, 12.7)
    async def eval_cases(self, agent_id: str) -> list[dict]:
        rows = [r for (a, _), r in self._eval_cases.items() if a == agent_id]
        return sorted(copy.deepcopy(rows), key=lambda r: (r["spec"].get("suite", ""), r["id"]))

    async def put_eval_case(self, agent_id: str, case_id: str, spec: dict, gate: bool, by: str | None = None) -> None:
        self._eval_cases[(agent_id, case_id)] = {"agent_id": agent_id, "id": case_id, "spec": copy.deepcopy(spec),
                                                 "gate": gate, "updated_by": by, "updated_at": _now()}

    async def delete_eval_case(self, agent_id: str, case_id: str) -> None:
        self._eval_cases.pop((agent_id, case_id), None)

    async def audit(self, agent_id: str, action: str, actor: str | None, detail: dict | None = None,
                    ws: str | None = None) -> None:
        self._audit.append({"id": len(self._audit) + 1, "ts": _now(), "agent_id": agent_id, "action": action,
                            "actor": actor, "detail": copy.deepcopy(detail or {}),
                            "workspace_id": ws or current_project()})

    async def audit_log(self, agent_id: str, limit: int = 100) -> list[dict]:
        return copy.deepcopy([r for r in reversed(self._audit) if r["agent_id"] == agent_id][:limit])

    async def project_audit_log(self, ws: str, limit: int = 200) -> list[dict]:
        return copy.deepcopy([r for r in reversed(self._audit) if r.get("workspace_id") == ws][:limit])

    async def meta_get(self, key: str) -> Any:
        return copy.deepcopy(self._meta.get(key))

    async def meta_set(self, key: str, value: Any) -> None:
        self._meta[key] = copy.deepcopy(value)


    # ---- knowledge base (12.9)
    async def kb_items(self, ws: str) -> list[dict]:
        items = self.__dict__.setdefault("_kb", {})
        return sorted((copy.deepcopy({k: v for k, v in i.items() if k != "content"}) for i in items.values()
                       if i["workspace_id"] == ws), key=lambda i: i["created_at"], reverse=True)

    async def kb_item(self, item_id: str) -> dict | None:
        return copy.deepcopy(self.__dict__.setdefault("_kb", {}).get(item_id))

    async def put_kb_item(self, row: dict) -> None:
        items = self.__dict__.setdefault("_kb", {})
        old = items.get(row["id"], {"created_at": _now(), "words": 0, "chunks": 0, "size_bytes": 0, "error": None,
                                    "status": "processing", "content": None, "extension": None})
        items[row["id"]] = {**old, **copy.deepcopy(row), "updated_at": _now()}

    async def delete_kb_item(self, item_id: str) -> None:
        self.__dict__.setdefault("_kb", {}).pop(item_id, None)
        self.__dict__["_kb_chunks"] = [c for c in self.__dict__.get("_kb_chunks", []) if c["item_id"] != item_id]

    async def set_kb_chunks(self, item_id: str, ws: str, chunks: list[tuple[int, str, list[float] | None]]) -> None:
        rest = [c for c in self.__dict__.get("_kb_chunks", []) if c["item_id"] != item_id]
        self.__dict__["_kb_chunks"] = rest + [{"item_id": item_id, "workspace_id": ws, "seq": s, "text": t,
                                               "embedding": e} for s, t, e in chunks]

    async def kb_search(self, ws: str, item_ids: list[str], query: str, vector: list[float] | None,
                        k: int = 4) -> list[dict]:
        """Vector + keyword ranks fused (RRF), like the PostgreSQL version."""
        import math
        names = {i["id"]: i["name"] for i in self.__dict__.get("_kb", {}).values()}
        pool = [c for c in self.__dict__.get("_kb_chunks", []) if c["workspace_id"] == ws and c["item_id"] in item_ids]
        words = {w for w in query.lower().split() if len(w) > 1}
        ranks: dict[int, float] = {}
        if vector:
            def cos(a, b):
                return sum(x * y for x, y in zip(a, b)) / ((math.sqrt(sum(x * x for x in a)) or 1) * (math.sqrt(sum(y * y for y in b)) or 1))
            scored = sorted(((cos(c["embedding"], vector), i) for i, c in enumerate(pool) if c["embedding"]), reverse=True)
            for r, (_, i) in enumerate(scored[:8]):
                ranks[i] = ranks.get(i, 0) + 1 / (60 + r)
        kw = sorted(((sum(w in c["text"].lower() for w in words), i) for i, c in enumerate(pool)), reverse=True)
        for r, (score, i) in enumerate([x for x in kw if x[0] > 0][:8]):
            ranks[i] = ranks.get(i, 0) + 1 / (60 + r)
        best = sorted(ranks, key=lambda i: -ranks[i])[:k]
        return [{"item_id": pool[i]["item_id"], "name": names.get(pool[i]["item_id"], ""), "seq": pool[i]["seq"],
                 "text": pool[i]["text"], "score": round(ranks[i], 5)} for i in best]

    async def kb_usage(self, ws: str) -> int:
        return sum(i["size_bytes"] for i in self.__dict__.get("_kb", {}).values() if i["workspace_id"] == ws)


def _j(v: Any) -> Any:
    return v if not isinstance(v, str) else json.loads(v)


def _agent_row(r: dict) -> dict:
    return {**r, "draft": _j(r["draft"]) if r.get("draft") is not None else None,
            "gate": _j(r["gate"]) if r.get("gate") is not None else None}


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

    async def list_workspaces(self) -> list[dict]:
        return await self._fetch("SELECT id, name, created_at FROM workspaces ORDER BY created_at, id")

    async def rename_workspace(self, ws: str, name: str) -> None:
        await self._exec("UPDATE workspaces SET name = $2 WHERE id = $1", ws, name)

    async def all_routes(self) -> list[dict]:
        return await self._fetch("SELECT * FROM phone_routes ORDER BY priority DESC")

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
        return [_agent_row(r) for r in rows]

    async def agent(self, agent_id: str) -> dict | None:
        r = await self._row("SELECT * FROM agents WHERE id = $1", agent_id)
        return _agent_row(r) if r else None

    async def set_gate(self, agent_id: str, gate: dict | None) -> None:
        await self._exec("UPDATE agents SET gate = $2::jsonb WHERE id = $1", agent_id,
                         json.dumps(gate, default=str) if gate is not None else None)

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
            await c.execute("DELETE FROM agent_eval_cases WHERE agent_id = $1", agent_id)
            await c.execute("DELETE FROM agent_shares WHERE agent_id = $1", agent_id)
            await c.execute("DELETE FROM agents WHERE id = $1", agent_id)

    async def delete_route(self, ws: str, pattern: str) -> None:
        await self._exec("DELETE FROM phone_routes WHERE workspace_id = $1 AND pattern = $2", ws, pattern)

    async def release(self, release_id: int) -> dict | None:
        r = await self._row("SELECT * FROM agent_releases WHERE id = $1", release_id)
        return {**r, "bundle": _j(r["bundle"]), "gate": _j(r.get("gate"))} if r else None

    async def releases(self, agent_id: str, limit: int = 30) -> list[dict]:
        rows = await self._fetch("SELECT id, version, author, note, created_at, gate FROM agent_releases "
                                 "WHERE agent_id = $1 ORDER BY version DESC LIMIT $2", agent_id, limit)
        return [{**r, "gate": _j(r["gate"])} for r in rows]

    async def add_release(self, agent_id: str, bundle: dict, author: str, note: str, *, publish: bool = True,
                          gate: dict | None = None) -> dict:
        async with (await self._p()).acquire() as c, c.transaction():
            await c.execute("SELECT 1 FROM agents WHERE id = $1 FOR UPDATE", agent_id)
            version = 1 + await c.fetchval("SELECT coalesce(max(version), 0) FROM agent_releases WHERE agent_id = $1",
                                           agent_id)
            rid = await c.fetchval("INSERT INTO agent_releases (agent_id, version, bundle, author, note, gate) "
                                   "VALUES ($1, $2, $3::jsonb, $4, $5, $6::jsonb) RETURNING id",
                                   agent_id, version, json.dumps(bundle, default=str), author, note,
                                   json.dumps(gate, default=str) if gate is not None else None)
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

    async def put_route(self, ws: str, pattern: str, agent_id: str, priority: int = 0, label: str | None = None) -> None:
        """`label`: None keeps the current one, "" clears it."""
        await self._exec("INSERT INTO phone_routes (workspace_id, pattern, agent_id, priority, label) "
                         "VALUES ($1, $2, $3, $4, NULLIF($5::text, '')) "
                         "ON CONFLICT (workspace_id, pattern) DO UPDATE SET agent_id = EXCLUDED.agent_id, "
                         "priority = EXCLUDED.priority, "
                         "label = CASE WHEN $5::text IS NULL THEN phone_routes.label ELSE NULLIF($5::text, '') END",
                         ws, pattern, agent_id, priority, label)

    # ---- users, sessions, members, invitations (console sign-in)
    async def count_users(self) -> int:
        r = await self._row("SELECT count(*) AS n FROM console_users")
        return r["n"]

    async def create_user(self, row: dict) -> None:
        await self._exec("INSERT INTO console_users (id, email, name, password_hash) VALUES ($1, $2, $3, $4)",
                         row["id"], row["email"].lower(), row.get("name", ""), row["password_hash"])

    async def user(self, user_id: str) -> dict | None:
        return await self._row("SELECT * FROM console_users WHERE id = $1", user_id)

    async def user_by_email(self, email: str) -> dict | None:
        return await self._row("SELECT * FROM console_users WHERE email = $1", email.lower())

    async def update_user(self, user_id: str, **fields) -> None:
        allowed = {k: v for k, v in fields.items() if k in ("name", "password_hash", "default_project", "last_login_at")}
        sets = ", ".join(f"{k} = ${i}" for i, k in enumerate(allowed, 2))
        await self._exec(f"UPDATE console_users SET {sets} WHERE id = $1", user_id, *allowed.values())

    async def create_session(self, token_hash: str, user_id: str, expires_at) -> None:
        await self._exec("INSERT INTO console_sessions (token_hash, user_id, expires_at) VALUES ($1, $2, $3)",
                         token_hash, user_id, expires_at)

    async def session_user(self, token_hash: str) -> dict | None:
        return await self._row("SELECT u.* FROM console_sessions s JOIN console_users u ON u.id = s.user_id "
                               "WHERE s.token_hash = $1 AND s.expires_at > now()", token_hash)

    async def delete_session(self, token_hash: str) -> None:
        await self._exec("DELETE FROM console_sessions WHERE token_hash = $1", token_hash)

    async def add_member(self, ws: str, user_id: str, role: str) -> None:
        await self._exec("INSERT INTO project_members (workspace_id, user_id, role) VALUES ($1, $2, $3) "
                         "ON CONFLICT (workspace_id, user_id) DO UPDATE SET role = EXCLUDED.role", ws, user_id, role)

    async def members(self, ws: str) -> list[dict]:
        return await self._fetch("SELECT m.*, u.email, u.name FROM project_members m JOIN console_users u "
                                 "ON u.id = m.user_id WHERE m.workspace_id = $1 ORDER BY m.joined_at", ws)

    async def memberships(self, user_id: str) -> list[dict]:
        return await self._fetch("SELECT * FROM project_members WHERE user_id = $1", user_id)

    async def remove_member(self, ws: str, user_id: str) -> None:
        await self._exec("DELETE FROM project_members WHERE workspace_id = $1 AND user_id = $2", ws, user_id)

    async def set_member_label(self, ws: str, user_id: str, label: str | None) -> None:
        await self._exec("UPDATE project_members SET label = $3 WHERE workspace_id = $1 AND user_id = $2",
                         ws, user_id, label)

    async def create_invitation(self, row: dict) -> None:
        await self._exec("INSERT INTO project_invitations (id, workspace_id, email, role, token_hash, invited_by, "
                         "expires_at) VALUES ($1, $2, $3, $4, $5, $6, $7)", row["id"], row["workspace_id"],
                         row["email"].lower(), row.get("role", "admin"), row["token_hash"], row.get("invited_by"),
                         row["expires_at"])

    async def invitations(self, ws: str) -> list[dict]:
        return await self._fetch("SELECT * FROM project_invitations WHERE workspace_id = $1 ORDER BY created_at", ws)

    async def invitation_by_token(self, token_hash: str) -> dict | None:
        return await self._row("SELECT * FROM project_invitations WHERE token_hash = $1", token_hash)

    async def update_invitation(self, inv_id: str, **fields) -> None:
        allowed = {k: v for k, v in fields.items() if k in ("token_hash", "expires_at", "accepted_at", "role")}
        sets = ", ".join(f"{k} = ${i}" for i, k in enumerate(allowed, 2))
        await self._exec(f"UPDATE project_invitations SET {sets} WHERE id = $1", inv_id, *allowed.values())

    async def delete_invitation(self, inv_id: str) -> None:
        await self._exec("DELETE FROM project_invitations WHERE id = $1", inv_id)

    # ---- test cases + audit (publish gate, 12.7)
    async def share(self, agent_id: str) -> dict | None:
        r = await self._row("SELECT * FROM agent_shares WHERE agent_id = $1", agent_id)
        return {**r, "settings": _j(r["settings"])} if r else None

    async def share_by_token(self, token: str) -> dict | None:
        r = await self._row("SELECT * FROM agent_shares WHERE token = $1", token)
        return {**r, "settings": _j(r["settings"])} if r else None

    async def put_share(self, row: dict) -> None:
        await self._exec("INSERT INTO agent_shares (agent_id, workspace_id, token, settings, created_by) "
                         "VALUES ($1, $2, $3, $4::jsonb, $5) ON CONFLICT (agent_id) DO UPDATE SET "
                         "settings = EXCLUDED.settings, updated_at = now()", row["agent_id"], row["workspace_id"], row["token"],
                         json.dumps(row.get("settings") or {}, ensure_ascii=False), row.get("created_by"))

    async def delete_share(self, agent_id: str) -> None:
        await self._exec("DELETE FROM agent_shares WHERE agent_id = $1", agent_id)

    # ---- API keys
    async def create_api_key(self, row: dict) -> None:
        await self._exec("INSERT INTO api_keys (id, workspace_id, name, prefix, key_hash, scope, created_by, expires_at) "
                         "VALUES ($1, $2, $3, $4, $5, $6, $7, $8)", row["id"], row["workspace_id"], row["name"], row["prefix"],
                         row["key_hash"], row["scope"], row.get("created_by"), row.get("expires_at"))

    async def api_key_by_hash(self, key_hash: str) -> dict | None:
        return await self._row("SELECT * FROM api_keys WHERE key_hash = $1", key_hash)

    async def api_keys(self, ws: str) -> list[dict]:
        return await self._fetch("SELECT * FROM api_keys WHERE workspace_id = $1 ORDER BY created_at DESC", ws)

    async def revoke_api_key(self, ws: str, key_id: str) -> bool:
        r = await self._row("UPDATE api_keys SET revoked_at = now() WHERE id = $1 AND workspace_id = $2 AND revoked_at IS NULL "
                            "RETURNING id", key_id, ws)
        return r is not None

    async def touch_api_key(self, key_id: str) -> None:
        await self._exec("UPDATE api_keys SET last_used_at = now() WHERE id = $1", key_id)

    # ---- post-call analysis
    async def put_call_analysis(self, row: dict) -> None:
        await self._exec(
            "INSERT INTO call_analysis (call_id, workspace_id, agent_id, started_at, status, error, summary, sentiment, csat, nps, "
            "resolved, outcome, model) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12::jsonb, $13) "
            "ON CONFLICT (call_id) DO UPDATE SET status = EXCLUDED.status, error = EXCLUDED.error, summary = EXCLUDED.summary, "
            "sentiment = EXCLUDED.sentiment, csat = EXCLUDED.csat, nps = EXCLUDED.nps, resolved = EXCLUDED.resolved, "
            "outcome = EXCLUDED.outcome, model = EXCLUDED.model, created_at = now()",
            row["call_id"], row["workspace_id"], row.get("agent_id"), row.get("started_at"), row["status"], row.get("error"),
            row.get("summary"), row.get("sentiment"), row.get("csat"), row.get("nps"), row.get("resolved"),
            json.dumps(row.get("outcome") or {}, ensure_ascii=False), row.get("model"))

    async def call_analysis(self, call_id: str) -> dict | None:
        r = await self._row("SELECT * FROM call_analysis WHERE call_id = $1", call_id)
        return {**r, "outcome": _j(r["outcome"])} if r else None

    # ---- call recordings (metadata; the audio is a file)
    async def put_recording(self, row: dict) -> None:
        await self._exec(
            "INSERT INTO call_recordings (call_id, workspace_id, agent_id, status, path, size_bytes, duration_s, error, started_at, expires_at) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10) ON CONFLICT (call_id) DO UPDATE SET status = EXCLUDED.status, "
            "path = EXCLUDED.path, size_bytes = EXCLUDED.size_bytes, duration_s = EXCLUDED.duration_s, error = EXCLUDED.error, "
            "expires_at = EXCLUDED.expires_at",
            row["call_id"], row["workspace_id"], row.get("agent_id"), row["status"], row.get("path"), row.get("size_bytes"),
            row.get("duration_s"), row.get("error"), row.get("started_at"), row["expires_at"])

    async def recording(self, call_id: str) -> dict | None:
        r = await self._row("SELECT * FROM call_recordings WHERE call_id = $1", call_id)
        return dict(r) if r else None

    async def due_recordings(self, now=None) -> list[dict]:
        rows = await self._fetch("SELECT * FROM call_recordings WHERE status = 'ok' AND expires_at <= COALESCE($1, now())", now)
        return [dict(r) for r in rows]

    async def end_recording(self, call_id: str, status: str) -> None:
        await self._exec("UPDATE call_recordings SET status = $2, path = NULL WHERE call_id = $1", call_id, status)

    async def call_analyses(self, ws: str, start=None, end=None, agent_id: str | None = None) -> list[dict]:
        where, args = ["workspace_id = $1"], [ws]
        for cond, val in (("started_at >= ${}", start), ("started_at < ${}", end), ("agent_id = ${}", agent_id)):
            if val is not None:
                args.append(val)
                where.append(cond.format(len(args)))
        rows = await self._fetch(f"SELECT call_id, agent_id, started_at, status, sentiment, csat, nps, resolved FROM call_analysis "
                                 f"WHERE {' AND '.join(where)}", *args)
        return [dict(r) for r in rows]

    # ---- webhook delivery history
    async def add_webhook_delivery(self, row: dict) -> None:
        await self._exec("INSERT INTO webhook_deliveries (agent_id, workspace_id, call_id, event, ok, detail, attempts) "
                         "VALUES ($1, $2, $3, $4, $5, $6, $7)", row["agent_id"], row["workspace_id"], row.get("call_id"),
                         row["event"], row["ok"], row.get("detail"), row.get("attempts", 1))
        await self._exec("DELETE FROM webhook_deliveries WHERE agent_id = $1 AND id NOT IN "
                         "(SELECT id FROM webhook_deliveries WHERE agent_id = $1 ORDER BY id DESC LIMIT 200)", row["agent_id"])

    async def webhook_deliveries(self, agent_id: str, limit: int = 50) -> list[dict]:
        return await self._fetch("SELECT * FROM webhook_deliveries WHERE agent_id = $1 ORDER BY id DESC LIMIT $2",
                                 agent_id, limit)

    # ---- batch (outbound) calls
    async def outbound_numbers(self, ws: str) -> list[dict]:
        rows = await self._fetch("SELECT * FROM outbound_numbers WHERE workspace_id = $1 ORDER BY number", ws)
        return [{**r, "dial_auth": _j(r["dial_auth"])} for r in rows]

    async def put_outbound_number(self, row: dict) -> None:
        await self._exec("INSERT INTO outbound_numbers (workspace_id, number, label, dial_url, dial_auth) "
                         "VALUES ($1, $2, $3, $4, $5::jsonb) ON CONFLICT (workspace_id, number) DO UPDATE SET "
                         "label = EXCLUDED.label, dial_url = EXCLUDED.dial_url, dial_auth = EXCLUDED.dial_auth",
                         row["workspace_id"], row["number"], row.get("label"), row["dial_url"],
                         json.dumps(row["dial_auth"]) if row.get("dial_auth") else None)

    async def delete_outbound_number(self, ws: str, number: str) -> None:
        await self._exec("DELETE FROM outbound_numbers WHERE workspace_id = $1 AND number = $2", ws, number)

    @staticmethod
    def _batch(r: dict | None) -> dict | None:
        return {**r, "config": _j(r["config"])} if r else None

    async def put_batch(self, row: dict) -> None:
        cols = [c for c in ("workspace_id", "name", "agent_id", "from_number", "status", "config", "created_by",
                            "started_at", "finished_at") if c in row]
        vals = [json.dumps(row[c]) if c == "config" else row[c] for c in cols]
        ph = [f"${i + 2}::jsonb" if c == "config" else f"${i + 2}" for i, c in enumerate(cols)]
        if "workspace_id" not in row:
            sets = ", ".join(f"{c} = {p}" for c, p in zip(cols, ph))
            await self._exec(f"UPDATE batch_calls SET {sets}, updated_at = now() WHERE id = $1", row["id"], *vals)
            return
        sets = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols)
        await self._exec(f"INSERT INTO batch_calls (id, {', '.join(cols)}) VALUES ($1, {', '.join(ph)}) "
                         f"ON CONFLICT (id) DO UPDATE SET {sets}, updated_at = now()", row["id"], *vals)

    async def batch(self, batch_id: str) -> dict | None:
        return self._batch(await self._row("SELECT * FROM batch_calls WHERE id = $1", batch_id))

    async def batches(self, ws: str) -> list[dict]:
        return [self._batch(r) for r in await self._fetch(
            "SELECT * FROM batch_calls WHERE workspace_id = $1 ORDER BY created_at DESC", ws)]

    async def batches_in(self, statuses: list[str]) -> list[dict]:
        return [self._batch(r) for r in await self._fetch("SELECT * FROM batch_calls WHERE status = ANY($1::text[])", statuses)]

    async def delete_batch(self, batch_id: str) -> None:
        await self._exec("DELETE FROM batch_calls WHERE id = $1", batch_id)

    async def add_recipients(self, batch_id: str, rows: list[dict]) -> int:
        async with (await self._p()).acquire() as c:
            await c.executemany("INSERT INTO batch_recipients (batch_id, phone, name, variables) VALUES ($1, $2, $3, $4::jsonb)",
                                [(batch_id, r["phone"], r.get("name"), json.dumps(r.get("variables") or {}, ensure_ascii=False))
                                 for r in rows])
        return len(rows)

    @staticmethod
    def _rec(r: dict) -> dict:
        return {**r, "variables": _j(r["variables"])}

    async def recipients(self, batch_id: str, status: str | None = None, q: str | None = None,
                         limit: int = 100, offset: int = 0) -> tuple[list[dict], int]:
        where, args = ["batch_id = $1"], [batch_id]
        if status:
            args.append(status)
            where.append(f"status = ${len(args)}")
        if q:
            args.append(f"%{q.lower()}%")
            where.append(f"(lower(phone) LIKE ${len(args)} OR lower(coalesce(name, '')) LIKE ${len(args)})")
        w = " AND ".join(where)
        total = (await self._row(f"SELECT count(*)::int AS n FROM batch_recipients WHERE {w}", *args))["n"]
        rows = await self._fetch(f"SELECT * FROM batch_recipients WHERE {w} ORDER BY id LIMIT {int(limit)} OFFSET {int(offset)}", *args)
        return [self._rec(r) for r in rows], total

    async def recipient(self, rid: int) -> dict | None:
        r = await self._row("SELECT * FROM batch_recipients WHERE id = $1", rid)
        return self._rec(r) if r else None

    async def recipient_counts(self, batch_id: str) -> dict[str, int]:
        rows = await self._fetch("SELECT status, count(*)::int AS n FROM batch_recipients WHERE batch_id = $1 GROUP BY status", batch_id)
        return {r["status"]: r["n"] for r in rows}

    async def update_recipient(self, rid: int, patch: dict) -> None:
        cols = [c for c in ("status", "call_id", "error", "attempts", "dialed_at", "ended_at", "duration_s") if c in patch]
        if cols:
            sets = ", ".join(f"{c} = ${i + 2}" for i, c in enumerate(cols))
            await self._exec(f"UPDATE batch_recipients SET {sets} WHERE id = $1", rid, *(patch[c] for c in cols))

    async def pending_recipients(self, batch_id: str, limit: int) -> list[dict]:
        rows = await self._fetch("SELECT * FROM batch_recipients WHERE batch_id = $1 AND status = 'pending' "
                                 "ORDER BY id LIMIT $2", batch_id, limit)
        return [self._rec(r) for r in rows]

    async def recipients_with_status(self, statuses: list[str]) -> list[dict]:
        return [self._rec(r) for r in await self._fetch(
            "SELECT * FROM batch_recipients WHERE status = ANY($1::text[])", statuses)]

    async def reset_recipients(self, batch_id: str, statuses: list[str]) -> int:
        r = await self._row("WITH u AS (UPDATE batch_recipients SET status = 'pending', error = NULL, call_id = NULL, "
                            "dialed_at = NULL, ended_at = NULL, duration_s = NULL WHERE batch_id = $1 "
                            "AND status = ANY($2::text[]) RETURNING 1) SELECT count(*)::int AS n FROM u", batch_id, statuses)
        return r["n"]

    async def delete_recipient(self, batch_id: str, rid: int) -> None:
        await self._exec("DELETE FROM batch_recipients WHERE id = $1 AND batch_id = $2", rid, batch_id)

    async def eval_cases(self, agent_id: str) -> list[dict]:
        rows = await self._fetch("SELECT * FROM agent_eval_cases WHERE agent_id = $1", agent_id)
        rows = [{**r, "spec": _j(r["spec"])} for r in rows]
        return sorted(rows, key=lambda r: (r["spec"].get("suite", ""), r["id"]))

    async def put_eval_case(self, agent_id: str, case_id: str, spec: dict, gate: bool, by: str | None = None) -> None:
        await self._exec("INSERT INTO agent_eval_cases (agent_id, id, spec, gate, updated_by) "
                         "VALUES ($1, $2, $3::jsonb, $4, $5) ON CONFLICT (agent_id, id) DO UPDATE SET "
                         "spec = EXCLUDED.spec, gate = EXCLUDED.gate, updated_by = EXCLUDED.updated_by, "
                         "updated_at = now()", agent_id, case_id, json.dumps(spec, ensure_ascii=False), gate, by)

    async def delete_eval_case(self, agent_id: str, case_id: str) -> None:
        await self._exec("DELETE FROM agent_eval_cases WHERE agent_id = $1 AND id = $2", agent_id, case_id)

    async def audit(self, agent_id: str, action: str, actor: str | None, detail: dict | None = None,
                    ws: str | None = None) -> None:
        await self._exec("INSERT INTO agent_audit (agent_id, action, actor, detail, workspace_id) "
                         "VALUES ($1, $2, $3, $4::jsonb, $5)", agent_id, action, actor,
                         json.dumps(detail or {}, ensure_ascii=False, default=str), ws or current_project())

    async def audit_log(self, agent_id: str, limit: int = 100) -> list[dict]:
        rows = await self._fetch("SELECT * FROM agent_audit WHERE agent_id = $1 ORDER BY ts DESC, id DESC LIMIT $2",
                                 agent_id, limit)
        return [{**r, "detail": _j(r["detail"])} for r in rows]

    async def project_audit_log(self, ws: str, limit: int = 200) -> list[dict]:
        rows = await self._fetch("SELECT * FROM agent_audit WHERE workspace_id = $1 ORDER BY ts DESC, id DESC "
                                 "LIMIT $2", ws, limit)
        return [{**r, "detail": _j(r["detail"])} for r in rows]

    async def meta_get(self, key: str) -> Any:
        r = await self._row("SELECT value FROM platform_meta WHERE key = $1", key)
        return _j(r["value"]) if r else None

    async def meta_set(self, key: str, value: Any) -> None:
        await self._exec("INSERT INTO platform_meta (key, value) VALUES ($1, $2::jsonb) "
                         "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", key, json.dumps(value, default=str))

    # ---- knowledge base (12.9)
    async def kb_items(self, ws: str) -> list[dict]:
        return await self._fetch("SELECT id, workspace_id, name, type, extension, size_bytes, words, chunks, status, "
                                 "error, source_url, created_by, created_at, updated_at FROM kb_items WHERE workspace_id = $1 "
                                 "ORDER BY created_at DESC", ws)

    async def kb_item(self, item_id: str) -> dict | None:
        return await self._row("SELECT * FROM kb_items WHERE id = $1", item_id)

    async def put_kb_item(self, row: dict) -> None:
        cols = [c for c in ("workspace_id", "name", "type", "extension", "size_bytes", "words", "chunks", "status",
                            "error", "content", "created_by", "source_url") if c in row]
        args = [row["id"], *(row[c] for c in cols)]
        if "workspace_id" not in row:                  # a status / counts update of an existing item
            sets = ", ".join(f"{c} = ${i + 2}" for i, c in enumerate(cols))
            await self._exec(f"UPDATE kb_items SET {sets}, updated_at = now() WHERE id = $1", *args)
            return
        sets = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols)
        await self._exec(f"INSERT INTO kb_items (id, {', '.join(cols)}) VALUES "
                         f"({', '.join(f'${i + 1}' for i in range(len(args)))}) "
                         f"ON CONFLICT (id) DO UPDATE SET {sets}, updated_at = now()", *args)

    async def delete_kb_item(self, item_id: str) -> None:
        await self._exec("DELETE FROM kb_items WHERE id = $1", item_id)

    async def set_kb_chunks(self, item_id: str, ws: str, chunks: list[tuple[int, str, list[float] | None]]) -> None:
        async with (await self._p()).acquire() as c, c.transaction():
            await c.execute("DELETE FROM kb_chunks WHERE item_id = $1", item_id)
            await c.executemany(
                "INSERT INTO kb_chunks (item_id, workspace_id, seq, text, embedding) VALUES ($1, $2, $3, $4, $5::vector)",
                [(item_id, ws, s, t, json.dumps(e) if e else None) for s, t, e in chunks])

    async def kb_search(self, ws: str, item_ids: list[str], query: str, vector: list[float] | None,
                        k: int = 4) -> list[dict]:
        """The best chunks of these items: vector similarity and keyword match, fused by reciprocal rank."""
        vec_sql = ("SELECT id, row_number() OVER (ORDER BY embedding <=> $3::vector) AS r FROM kb_chunks "
                   "WHERE workspace_id = $1 AND item_id = ANY($2::text[]) AND embedding IS NOT NULL "
                   "ORDER BY embedding <=> $3::vector LIMIT 8") if vector else \
                  "SELECT NULL::bigint AS id, NULL::bigint AS r WHERE $3::text IS NULL AND false"
        sql = f"""
            WITH v AS ({vec_sql}),
                 t AS (SELECT id, row_number() OVER (ORDER BY ts_rank(to_tsvector('simple', text), q) DESC) AS r
                       FROM kb_chunks, plainto_tsquery('simple', $4) q
                       WHERE workspace_id = $1 AND item_id = ANY($2::text[]) AND to_tsvector('simple', text) @@ q
                       ORDER BY ts_rank(to_tsvector('simple', text), q) DESC LIMIT 8),
                 f AS (SELECT id, sum(1.0 / (60 + r)) AS score FROM (SELECT * FROM v UNION ALL SELECT * FROM t) x
                       GROUP BY id)
            SELECT c.item_id, i.name, c.seq, c.text, round(f.score::numeric, 5)::float AS score
            FROM f JOIN kb_chunks c ON c.id = f.id JOIN kb_items i ON i.id = c.item_id
            ORDER BY f.score DESC LIMIT {int(k)}"""
        return await self._fetch(sql, ws, list(item_ids), json.dumps(vector) if vector else None, query)

    async def kb_usage(self, ws: str) -> int:
        r = await self._row("SELECT coalesce(sum(size_bytes), 0)::bigint AS n FROM kb_items WHERE workspace_id = $1", ws)
        return int(r["n"]) if r else 0
