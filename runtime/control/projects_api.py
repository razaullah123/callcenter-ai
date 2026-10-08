"""Projects (Hamsa's project switcher): separate sets of agents, skills, tools, MCP servers, connections, secrets
and phone routes. Projects are the `workspaces` table; the console sends the current one as X-Project.

    GET  /api/projects                 list (with what each holds)
    POST /api/projects                 create  {name, copy_setup}  — copy_setup: start with the current project's
                                       connections, secrets, tool library and MCP servers (servers renamed
                                       <name>-<project>; names are unique across projects)
    PUT  /api/projects/{id}            rename  {name}

No user accounts yet: every console user sees every project (members / invitations come with sign-in).
"""

import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from runtime.control.accounts import principal
from runtime.control.api import _rt, auth, changed, audited
from runtime.platform import WORKSPACE, current_project

router = APIRouter(prefix="/api")


def _store():
    rt = _rt()
    if rt.platform is None:
        raise HTTPException(503, "the platform database is not available")
    return rt, rt.platform


@router.get("/projects", dependencies=auth)
async def list_projects() -> list[dict]:
    """The caller's projects: "mine" (owner) and "invited" (admin, with the owner's name and the caller's own label)."""
    rt, store = _store()
    who = principal()
    out = []
    for w in await store.list_workspaces():
        role = who.role(w["id"])
        if role is None:
            continue
        member = who.memberships.get(w["id"])          # None: seen only because this is the platform owner
        members = await store.members(w["id"]) if who.is_user else []
        owner = next((m for m in members if m["role"] == "owner"), None)
        mine = member or {}
        out.append({"id": w["id"], "name": w["name"], "created_at": w.get("created_at"),
                    "role": role, "mine": (mine.get("role") == "owner") if who.is_user else role == "owner",
                    "access": "platform" if who.is_user and member is None else "member", "label": mine.get("label"),
                    "owner": (owner or {}).get("name") or (owner or {}).get("email"),
                    "default": (who.user or {}).get("default_project") == w["id"] if who.is_user else None,
                    "platform_default": w["id"] == WORKSPACE,
                    "agents": len(await store.agents(w["id"])),
                    "connections": len(await store.providers(w["id"])),
                    "mcp_servers": len(await store.mcp_servers(w["id"])),
                    "tools": len(await store.tools(w["id"])),
                    "secrets": len(await store.secrets(w["id"])),
                    "routes": [r["pattern"] for r in await store.routes(w["id"])]})
    return out


class NewProject(BaseModel):
    name: str
    copy_setup: bool = True
    author: str = "console"


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:32]


@router.post("/projects", dependencies=auth)
async def create_project(body: NewProject) -> dict:
    rt, store = _store()
    who = principal()
    if who.is_user and not who.platform and not rt.settings.invited_users_can_create_projects \
            and not any(m["role"] == "owner" for m in who.memberships.values()):
        raise HTTPException(403, "only the platform owner and project owners can create projects here")
    name = body.name.strip()
    if not name:
        raise HTTPException(422, {"errors": ["name: required"]})
    existing = {w["id"] for w in await store.list_workspaces()}
    base = _slug(name) or "project"
    pid, n = base, 2
    while pid in existing:
        pid, n = f"{base}-{n}", n + 1
    await store.ensure_workspace(pid, name)
    if principal().is_user:                                  # the creator owns it (Hamsa: "My projects")
        await store.add_member(pid, principal().user["id"], "owner")
    copied = await _copy_setup(store, current_project(), pid, body.author) if body.copy_setup else {}
    await store.audit("", "project.created", principal().actor, {"name": name, "from": current_project(),
                                                           "copied": copied}, ws=pid)
    if copied.get("mcp_servers"):
        await changed(rt, "mcp", project=pid)
    return {"id": pid, "name": name, "copied": copied}


async def _copy_setup(store, src: str, dst: str, by: str) -> dict:
    """Connections, secrets (still encrypted), the tool library and MCP servers of `src`, for a new project."""
    for s in await store.secrets(src):
        row = await store.secret(src, s["name"])
        await store.put_secret(dst, s["name"], row["ciphertext"], row.get("hint") or "", by)
    providers = await store.providers(src)
    for p in providers:
        await store.put_provider({**{k: v for k, v in p.items() if k not in ("updated_at",)},
                                  "id": f"{dst}-{p['id']}", "workspace_id": dst, "updated_by": by})
    tools = await store.tools(src)
    if tools:
        await store.put_tools(dst, [{k: t[k] for k in ("name", "grp", "source", "policy") if k in t} for t in tools])
    servers = await store.mcp_servers(src)
    for m in servers:
        new = f"{m['name']}-{dst}"
        await store.put_mcp_server({k: v for k, v in {**m, "id": new, "name": new, "workspace_id": dst}.items()
                                    if k != "updated_at"})
    return {"secrets": len(await store.secrets(dst)), "connections": len(providers), "tools": len(tools),
            "mcp_servers": len(servers)}


@router.get("/projects/{project_id}/audit", dependencies=auth)
async def project_audit(project_id: str, limit: int = 200) -> list[dict]:
    """Everything that changed in the project: agents (publish, rollback, drafts, test cases) and the project
    (connections, secrets, tools, MCP servers, routes, name)."""
    rt, store = _store()
    if not any(w["id"] == project_id for w in await store.list_workspaces()) or principal().role(project_id) is None:
        raise HTTPException(404, "project not found")
    return await store.project_audit_log(project_id, limit)


class Rename(BaseModel):
    name: str


@router.put("/projects/{project_id}", dependencies=auth)
async def rename_project(project_id: str, body: Rename) -> dict:
    """The project's name for everyone (owner only); members can rename it for themselves with a label."""
    rt, store = _store()
    if not any(w["id"] == project_id for w in await store.list_workspaces()) or principal().role(project_id) is None:
        raise HTTPException(404, "project not found")
    if principal().role(project_id) != "owner":
        raise HTTPException(403, "only the project owner can rename it — use Rename label for your own name")
    if not body.name.strip():
        raise HTTPException(422, {"errors": ["name: required"]})
    old = next(w["name"] for w in await store.list_workspaces() if w["id"] == project_id)
    await store.rename_workspace(project_id, body.name.strip())
    await store.audit("", "project.renamed", principal().actor, {"from": old, "to": body.name.strip()}, ws=project_id)
    return {"id": project_id, "name": body.name.strip()}
