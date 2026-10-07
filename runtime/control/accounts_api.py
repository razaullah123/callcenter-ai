"""Sign-in, team members and invitations (Hamsa's Project settings → Team members / Invite member).

    GET  /api/auth/status                      public — {mode: setup | login | open | signed_in, user?, mail}
    POST /api/auth/setup                       public, only while no account exists — the first account (owner of
                                               every existing project)        {name, email, password}
    POST /api/auth/login                       public                          {email, password} → {token, user}
    POST /api/auth/logout
    GET  /api/me · PUT /api/me {name?, password?, current_password?} · PUT /api/me/default-project {project}
    PUT  /api/projects/{id}/label              your own name for a project     {label}
    GET  /api/projects/{id}/members            members + invitations (Joined / Invited / Invitation expired)
    POST /api/projects/{id}/invitations        invite by email (owner / admin) {email, role} → {link, emailed}
    POST /api/projects/{id}/invitations/{inv}/resend   · DELETE …/invitations/{inv} (revoke)
    DELETE /api/projects/{id}/members/{user}   remove a member (owner / admin; never the owner)
    GET  /api/invitations/{token}              public — what the invitation is for
    POST /api/invitations/{token}/accept       public — join (creating the account if needed) → {token, user}
"""

import asyncio
import logging
import re
import uuid
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from runtime.control import accounts as acc
from runtime.control.accounts import principal
from runtime.control.api import _rt, auth, bearer_of

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _store():
    rt = _rt()
    if rt.platform is None:
        raise HTTPException(503, "the platform database is not available")
    return rt, rt.platform


# ---------------------------------------------------------------- sign-in

@router.get("/auth/status")
async def status(request: Request) -> dict:
    rt = _rt()
    store = rt.platform
    users = await store.count_users() if store is not None else 0
    who = await acc.resolve(store, rt.settings, bearer_of(request))
    out = {"accounts": users > 0, "mail": acc.mail_configured(rt.settings)}
    if who is not None and who.is_user:
        return {**out, "mode": "signed_in", "user": acc.public_user(who.user)}
    if who is not None and who.kind == "token":
        return {**out, "mode": "token"}
    if users == 0 and store is not None:
        return {**out, "mode": "setup", "open": who is not None}
    return {**out, "mode": "login"}


class Setup(BaseModel):
    name: str
    email: str
    password: str


@router.post("/auth/setup")
async def setup(body: Setup, request: Request) -> dict:
    rt, store = _store()
    if await store.count_users() > 0:
        raise HTTPException(409, "an account exists already — sign in")
    if rt.settings.console_token is not None and \
            (await acc.resolve(store, rt.settings, bearer_of(request)) or acc.Principal("none")).kind != "token":
        raise HTTPException(401, {"message": "the console token is required to create the first account", "login": True})
    errors = _check_account(body.email, body.password, body.name)
    if errors:
        raise HTTPException(422, {"errors": errors})
    user = await acc.create_user(store, body.email, body.name, body.password)
    for w in await store.list_workspaces():                       # the first account owns everything there is
        await store.add_member(w["id"], user["id"], "owner")
        await store.audit("", "member.joined", user["email"], {"email": user["email"], "role": "owner",
                                                               "how": "first account"}, ws=w["id"])
    return {"token": await acc.start_session(store, user), "user": acc.public_user(user)}


def _check_account(email: str, password: str, name: str | None = "x") -> list[str]:
    errors = []
    if not EMAIL.match((email or "").strip()):
        errors.append("email: not a valid address")
    if (e := acc.check_new_password(password)):
        errors.append(e)
    if name is not None and not name.strip():
        errors.append("name: required")
    return errors


class Login(BaseModel):
    email: str
    password: str


@router.post("/auth/login")
async def login(body: Login) -> dict:
    rt, store = _store()
    user = await store.user_by_email(body.email.strip())
    if user is None or not acc.check_password(body.password, user["password_hash"]):
        await asyncio.sleep(0.4)                                   # slows down password guessing
        raise HTTPException(401, {"message": "wrong email or password"})
    return {"token": await acc.start_session(store, user), "user": acc.public_user(user)}


@router.post("/auth/logout")
async def logout(request: Request) -> dict:
    rt, store = _store()
    if (b := bearer_of(request)) and b.startswith("s_"):
        await store.delete_session(acc.token_hash(b))
    return {"ok": True}


@router.get("/me", dependencies=auth)
async def me() -> dict:
    who = principal()
    return {"kind": who.kind, "user": acc.public_user(who.user) if who.is_user else None, "platform_owner": who.platform}


class MeBody(BaseModel):
    name: str | None = None
    password: str | None = None
    current_password: str | None = None


@router.put("/me", dependencies=auth)
async def update_me(body: MeBody) -> dict:
    rt, store = _store()
    who = principal()
    if not who.is_user:
        raise HTTPException(400, "not signed in as a user")
    fields = {}
    if body.name is not None and body.name.strip():
        fields["name"] = body.name.strip()
    if body.password:
        if not acc.check_password(body.current_password or "", who.user["password_hash"]):
            raise HTTPException(403, "the current password is wrong")
        if (e := acc.check_new_password(body.password)):
            raise HTTPException(422, {"errors": [e]})
        fields["password_hash"] = acc.hash_password(body.password)
    if fields:
        await store.update_user(who.user["id"], **fields)
    return {"user": acc.public_user(await store.user(who.user["id"]))}


class DefaultBody(BaseModel):
    project: str


@router.put("/me/default-project", dependencies=auth)
async def default_project(body: DefaultBody) -> dict:
    rt, store = _store()
    who = principal()
    if who.role(body.project) is None:
        raise HTTPException(404, "project not found")
    if who.is_user:
        await store.update_user(who.user["id"], default_project=body.project)
    return {"default_project": body.project}


class LabelBody(BaseModel):
    label: str = ""


@router.put("/projects/{project_id}/label", dependencies=auth)
async def set_label(project_id: str, body: LabelBody) -> dict:
    """Your own name for a project (Hamsa: "This label is only visible to you")."""
    rt, store = _store()
    who = principal()
    if not who.is_user or who.role(project_id) is None:
        raise HTTPException(404, "project not found")
    await store.set_member_label(project_id, who.user["id"], body.label.strip() or None)
    return {"label": body.label.strip() or None}


# ---------------------------------------------------------------- members + invitations

def _manage(project_id: str) -> None:
    if principal().role(project_id) not in ("owner", "admin"):
        raise HTTPException(404 if principal().role(project_id) is None else 403, "not allowed")


def _invite_view(i: dict) -> dict:
    status = "joined" if i.get("accepted_at") else "expired" if i["expires_at"] < acc.now() else "invited"
    return {"id": i["id"], "email": i["email"], "role": i["role"], "status": status, "invited_by": i.get("invited_by"),
            "created_at": i.get("created_at"), "expires_at": i["expires_at"]}


@router.get("/projects/{project_id}/members", dependencies=auth)
async def list_members(project_id: str) -> dict:
    rt, store = _store()
    if principal().role(project_id) is None:
        raise HTTPException(404, "project not found")
    members = [{"user_id": m["user_id"], "name": m.get("name") or m["email"].split("@")[0], "email": m["email"],
                "role": m["role"], "status": "joined", "joined_at": m.get("joined_at"),
                "you": principal().is_user and m["user_id"] == principal().user["id"]}
               for m in await store.members(project_id)]
    members.sort(key=lambda m: (m["role"] != "owner", str(m["joined_at"])))
    invites = [_invite_view(i) for i in await store.invitations(project_id) if not i.get("accepted_at")]
    return {"members": members, "invitations": invites, "can_manage": principal().role(project_id) in ("owner", "admin"),
            "you": principal().role(project_id), "mail": acc.mail_configured(rt.settings),
            "accounts": await store.count_users() > 0}


class InviteBody(BaseModel):
    email: str
    role: str = "admin"


def _link(request: Request, token: str) -> str:
    return f"{str(request.base_url).rstrip('/')}/console/invite/{token}"


async def _send(rt, request: Request, project_id: str, email: str, token: str) -> bool:
    if not acc.mail_configured(rt.settings):
        return False
    name = next((w["name"] for w in await rt.platform.list_workspaces() if w["id"] == project_id), project_id)
    try:
        await asyncio.to_thread(acc.send_invitation_email, rt.settings, email, name, principal().actor,
                                _link(request, token))
        return True
    except Exception as e:
        log.warning("invitation email to %s not sent: %r", email, e)
        return False


@router.post("/projects/{project_id}/invitations", dependencies=auth)
async def invite(project_id: str, body: InviteBody, request: Request) -> dict:
    rt, store = _store()
    _manage(project_id)
    email = body.email.strip().lower()
    if not EMAIL.match(email):
        raise HTTPException(422, {"errors": ["email: not a valid address"]})
    if body.role not in ("admin",):
        raise HTTPException(422, {"errors": ["role: admin (a project has one owner)"]})
    if any(m["email"] == email for m in await store.members(project_id)):
        raise HTTPException(409, f"{email} is already a member")
    for old in await store.invitations(project_id):               # a new invitation replaces a pending one
        if old["email"] == email and not old.get("accepted_at"):
            await store.delete_invitation(old["id"])
    token = acc.new_token("inv")
    inv = {"id": uuid.uuid4().hex[:16], "workspace_id": project_id, "email": email, "role": body.role,
           "token_hash": acc.token_hash(token), "invited_by": principal().actor,
           "expires_at": acc.now() + timedelta(days=acc.INVITE_DAYS)}
    await store.create_invitation(inv)
    await store.audit("", "member.invited", principal().actor, {"email": email, "role": body.role}, ws=project_id)
    emailed = await _send(rt, request, project_id, email, token)
    return {"invitation": _invite_view({**inv, "created_at": acc.now()}), "link": _link(request, token),
            "emailed": emailed}


@router.post("/projects/{project_id}/invitations/{inv_id}/resend", dependencies=auth)
async def resend(project_id: str, inv_id: str, request: Request) -> dict:
    rt, store = _store()
    _manage(project_id)
    inv = next((i for i in await store.invitations(project_id) if i["id"] == inv_id and not i.get("accepted_at")), None)
    if inv is None:
        raise HTTPException(404, "invitation not found")
    token = acc.new_token("inv")                                  # the old link stops working
    await store.update_invitation(inv_id, token_hash=acc.token_hash(token),
                                  expires_at=acc.now() + timedelta(days=acc.INVITE_DAYS))
    emailed = await _send(rt, request, project_id, inv["email"], token)
    return {"link": _link(request, token), "emailed": emailed}


@router.delete("/projects/{project_id}/invitations/{inv_id}", dependencies=auth)
async def revoke(project_id: str, inv_id: str) -> dict:
    rt, store = _store()
    _manage(project_id)
    inv = next((i for i in await store.invitations(project_id) if i["id"] == inv_id), None)
    if inv is None:
        raise HTTPException(404, "invitation not found")
    await store.delete_invitation(inv_id)
    await store.audit("", "member.invitation_revoked", principal().actor, {"email": inv["email"]}, ws=project_id)
    return {"ok": True}


@router.delete("/projects/{project_id}/members/{user_id}", dependencies=auth)
async def remove_member(project_id: str, user_id: str) -> dict:
    rt, store = _store()
    _manage(project_id)
    m = next((m for m in await store.members(project_id) if m["user_id"] == user_id), None)
    if m is None:
        raise HTTPException(404, "member not found")
    if m["role"] == "owner":
        raise HTTPException(409, "the project owner can't be removed")
    await store.remove_member(project_id, user_id)
    await store.audit("", "member.removed", principal().actor, {"email": m["email"]}, ws=project_id)
    return {"ok": True}


# ---------------------------------------------------------------- accepting an invitation (public, by link)

async def _invitation(store, token: str) -> dict:
    inv = await store.invitation_by_token(acc.token_hash(token))
    if inv is None:
        raise HTTPException(404, "this invitation link is not valid (it may have been revoked or sent again)")
    return inv


@router.get("/invitations/{token}")
async def invitation(token: str) -> dict:
    rt, store = _store()
    inv = await _invitation(store, token)
    name = next((w["name"] for w in await store.list_workspaces() if w["id"] == inv["workspace_id"]), "")
    view = _invite_view(inv)
    return {"project": name, "email": inv["email"], "invited_by": inv.get("invited_by"), "status": view["status"],
            "has_account": await store.user_by_email(inv["email"]) is not None}


class AcceptBody(BaseModel):
    password: str
    name: str = ""


@router.post("/invitations/{token}/accept")
async def accept(token: str, body: AcceptBody) -> dict:
    rt, store = _store()
    inv = await _invitation(store, token)
    view = _invite_view(inv)
    if view["status"] != "invited":
        raise HTTPException(410, "this invitation has expired — ask for a new one" if view["status"] == "expired"
                            else "this invitation was already used")
    user = await store.user_by_email(inv["email"])
    if user is None:
        if errors := _check_account(inv["email"], body.password, body.name):
            raise HTTPException(422, {"errors": errors})
        user = await acc.create_user(store, inv["email"], body.name, body.password)
    elif not acc.check_password(body.password, user["password_hash"]):
        await asyncio.sleep(0.4)
        raise HTTPException(401, {"message": "wrong password for this account"})
    await store.add_member(inv["workspace_id"], user["id"], inv["role"])
    await store.update_invitation(inv["id"], accepted_at=acc.now())
    await store.audit("", "member.joined", user["email"], {"email": user["email"], "role": inv["role"]},
                      ws=inv["workspace_id"])
    return {"token": await acc.start_session(store, user), "user": acc.public_user(user),
            "project": inv["workspace_id"]}
