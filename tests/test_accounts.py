"""Console sign-in, project members and invitations (Hamsa's Team members / Invite member / Invited projects)."""

from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from .test_projects import console as projects_console  # noqa: F401  (fixture)


@pytest.fixture
def client(projects_console):  # noqa: F811
    c, rt, store, loop = projects_console
    from runtime.control import accounts_api
    from runtime.control import keys_api
    c.app.include_router(accounts_api.router)
    c.app.include_router(keys_api.router)
    return c, rt, store, loop


def _h(token: str, project: str | None = None) -> dict:
    return {"Authorization": f"Bearer {token}", **({"X-Project": project} if project else {})}


def _setup(c) -> str:
    r = c.post("/api/auth/setup", json={"name": "Raza", "email": "Raza@Example.com", "password": "secret-pass"})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def test_first_account_owns_everything_then_sign_in_is_required(client):
    c, rt, store, loop = client
    assert c.get("/api/auth/status").json()["mode"] == "setup"
    assert c.get("/api/agents").status_code == 200                          # open until the first account
    token = _setup(c)
    assert c.post("/api/auth/setup", json={"name": "x", "email": "x@y.zz", "password": "12345678"}).status_code == 409
    assert c.get("/api/agents").status_code == 401                          # now: sign in
    assert c.get("/api/auth/status").json()["mode"] == "login"
    me = c.get("/api/auth/status", headers=_h(token)).json()
    assert me["mode"] == "signed_in" and me["user"]["email"] == "raza@example.com"
    projects = c.get("/api/projects", headers=_h(token)).json()
    assert [(p["id"], p["role"], p["mine"]) for p in projects] == [("hmg", "owner", True)]
    assert c.post("/api/auth/login", json={"email": "raza@example.com", "password": "nope-nope"}).status_code == 401
    t2 = c.post("/api/auth/login", json={"email": "RAZA@example.com", "password": "secret-pass"}).json()["token"]
    assert c.get("/api/agents", headers=_h(t2)).status_code == 200
    c.post("/api/auth/logout", headers=_h(t2))
    assert c.get("/api/agents", headers=_h(t2)).status_code == 401


def test_invite_accept_and_invited_projects(client):
    c, rt, store, loop = client
    owner = _setup(c)
    r = c.post("/api/projects/hmg/invitations", json={"email": "sara@example.com"}, headers=_h(owner))
    assert r.status_code == 200, r.text
    link = r.json()["link"]
    assert "/console/invite/inv_" in link and r.json()["emailed"] is False      # no SMTP: the link is shown
    members = c.get("/api/projects/hmg/members", headers=_h(owner)).json()
    assert [m["role"] for m in members["members"]] == ["owner"]
    assert [(i["email"], i["status"]) for i in members["invitations"]] == [("sara@example.com", "invited")]
    token = link.rsplit("/", 1)[1]
    info = c.get(f"/api/invitations/{token}").json()
    assert info == {"project": "Dr. Sulaiman Al Habib Group", "email": "sara@example.com",
                    "invited_by": "raza@example.com", "status": "invited", "has_account": False}
    assert c.post(f"/api/invitations/{token}/accept", json={"password": "short"}).status_code == 422
    joined = c.post(f"/api/invitations/{token}/accept", json={"name": "Sara", "password": "sara-pass-1"})
    assert joined.status_code == 200, joined.text
    sara = joined.json()["token"]
    assert c.post(f"/api/invitations/{token}/accept", json={"password": "sara-pass-1"}).status_code == 410
    mine = c.get("/api/projects", headers=_h(sara)).json()
    assert [(p["id"], p["role"], p["mine"], p["owner"]) for p in mine] == [("hmg", "admin", False, "Raza")]
    # a project Sara creates is hers; Raza, the platform owner, sees it too (but it is not "his")
    c.post("/api/projects", json={"name": "Sara Lab", "copy_setup": False}, headers=_h(sara))
    assert {p["id"]: p["mine"] for p in c.get("/api/projects", headers=_h(sara)).json()} == {"hmg": False, "sara-lab": True}
    assert {p["id"]: (p["mine"], p["access"]) for p in c.get("/api/projects", headers=_h(owner)).json()} == {"hmg": (True, "member"), "sara-lab": (False, "platform")}
    assert c.get("/api/agents", headers=_h(owner, "sara-lab")).status_code == 200
    # her own label for HMG; only the owner renames the project for everyone
    c.put("/api/projects/hmg/label", json={"label": "Hospital"}, headers=_h(sara))
    assert next(p for p in c.get("/api/projects", headers=_h(sara)).json() if p["id"] == "hmg")["label"] == "Hospital"
    assert next(p for p in c.get("/api/projects", headers=_h(owner)).json())["label"] is None
    assert c.put("/api/projects/hmg", json={"name": "X"}, headers=_h(sara)).status_code == 403
    # ★ default project per user
    c.put("/api/me/default-project", json={"project": "sara-lab"}, headers=_h(sara))
    assert {p["id"]: p["default"] for p in c.get("/api/projects", headers=_h(sara)).json()}["sara-lab"] is True
    # the audit log names who did it
    actions = {(a["action"], a["actor"]) for a in c.get("/api/projects/hmg/audit", headers=_h(owner)).json()}
    assert ("member.invited", "raza@example.com") in actions and ("member.joined", "sara@example.com") in actions


def test_remove_members_and_revoke_invitations(client):
    c, rt, store, loop = client
    owner = _setup(c)
    link = c.post("/api/projects/hmg/invitations", json={"email": "a@example.com"}, headers=_h(owner)).json()["link"]
    a = c.post(f"/api/invitations/{link.rsplit('/', 1)[1]}/accept", json={"name": "A", "password": "aaaa-aaaa"}).json()["token"]
    members = c.get("/api/projects/hmg/members", headers=_h(owner)).json()["members"]
    owner_id = next(m["user_id"] for m in members if m["role"] == "owner")
    a_id = next(m["user_id"] for m in members if m["role"] == "admin")
    assert c.delete(f"/api/projects/hmg/members/{owner_id}", headers=_h(a)).status_code == 409   # never the owner
    assert c.post("/api/projects/hmg/invitations", json={"email": "a@example.com"}, headers=_h(owner)).status_code == 409
    second = c.post("/api/projects/hmg/invitations", json={"email": "b@example.com"}, headers=_h(a)).json()
    inv_id = second["invitation"]["id"]
    resent = c.post(f"/api/projects/hmg/invitations/{inv_id}/resend", headers=_h(owner)).json()["link"]
    assert c.get(f"/api/invitations/{second['link'].rsplit('/', 1)[1]}").status_code == 404     # old link dead
    assert c.delete(f"/api/projects/hmg/invitations/{inv_id}", headers=_h(owner)).status_code == 200
    assert c.get(f"/api/invitations/{resent.rsplit('/', 1)[1]}").status_code == 404
    assert c.delete(f"/api/projects/hmg/members/{a_id}", headers=_h(owner)).status_code == 200
    assert c.get("/api/agents", headers=_h(a)).status_code == 404                 # no project left for A


def test_expired_invitation(client):
    c, rt, store, loop = client
    owner = _setup(c)
    link = c.post("/api/projects/hmg/invitations", json={"email": "late@example.com"}, headers=_h(owner)).json()["link"]
    inv = loop.run_until_complete(store.invitations("hmg"))[0]
    loop.run_until_complete(store.update_invitation(inv["id"], expires_at=inv["expires_at"] - timedelta(hours=3)))
    token = link.rsplit("/", 1)[1]
    assert c.get(f"/api/invitations/{token}").json()["status"] == "expired"
    assert c.post(f"/api/invitations/{token}/accept", json={"name": "L", "password": "llll-llll"}).status_code == 410
    assert c.get("/api/projects/hmg/members", headers=_h(owner)).json()["invitations"][0]["status"] == "expired"


def test_console_token_still_works(client, monkeypatch):
    c, rt, store, loop = client
    _setup(c)
    from pydantic import SecretStr
    monkeypatch.setattr(rt.settings, "console_token", SecretStr("script-token"))
    assert c.get("/api/agents", headers=_h("script-token")).status_code == 200
    assert c.get("/api/agents", headers=_h("wrong")).status_code == 401


def test_passwords_are_hashed():
    from runtime.control.accounts import check_password, hash_password
    h = hash_password("correct horse")
    assert "correct horse" not in h and check_password("correct horse", h) and not check_password("wrong", h)


def test_the_platform_owner_sees_every_project_and_others_do_not(client):
    c, rt, store, loop = client
    owner = _setup(c)                                                        # owner of the default project = platform owner
    link = c.post("/api/projects/hmg/invitations", json={"email": "sara@example.com"}, headers=_h(owner)).json()["link"]
    sara = c.post(f"/api/invitations/{link.rsplit('/', 1)[1]}/accept", json={"name": "Sara", "password": "sara-pass-1"}).json()["token"]
    c.post("/api/projects", json={"name": "Sara Lab", "copy_setup": False}, headers=_h(sara))
    c.post("/api/projects", json={"name": "Clinic", "copy_setup": False}, headers=_h(owner))
    assert c.get("/api/me", headers=_h(owner)).json()["platform_owner"] is True
    assert c.get("/api/me", headers=_h(sara)).json()["platform_owner"] is False
    assert {p["id"] for p in c.get("/api/projects", headers=_h(owner)).json()} == {"hmg", "sara-lab", "clinic"}
    assert {p["id"] for p in c.get("/api/projects", headers=_h(sara)).json()} == {"hmg", "sara-lab"}      # Sara never sees "clinic"
    assert c.get("/api/agents", headers=_h(sara, "clinic")).status_code == 404
    assert c.get("/api/projects/sara-lab/members", headers=_h(owner)).json()["can_manage"] is True       # manages it as the owner
    assert c.put("/api/projects/sara-lab", json={"name": "Sara's lab"}, headers=_h(owner)).status_code == 200
    assert c.get("/api/projects/clinic/members", headers=_h(sara)).status_code == 404


def test_platform_owner_emails_override_the_default(client):
    c, rt, store, loop = client
    owner = _setup(c)
    link = c.post("/api/projects/hmg/invitations", json={"email": "sara@example.com"}, headers=_h(owner)).json()["link"]
    sara = c.post(f"/api/invitations/{link.rsplit('/', 1)[1]}/accept", json={"name": "Sara", "password": "sara-pass-1"}).json()["token"]
    c.post("/api/projects", json={"name": "Sara Lab", "copy_setup": False}, headers=_h(sara))
    rt.settings = rt.settings.model_copy(update={"platform_owner_emails": " SARA@example.com , other@example.com"})
    assert c.get("/api/me", headers=_h(sara)).json()["platform_owner"] is True
    assert c.get("/api/me", headers=_h(owner)).json()["platform_owner"] is False                          # named list: only those
    assert {p["id"] for p in c.get("/api/projects", headers=_h(sara)).json()} == {"hmg", "sara-lab"}
    assert {p["id"] for p in c.get("/api/projects", headers=_h(owner)).json()} == {"hmg"}


def _join(c, owner, email, **extra):
    link = c.post("/api/projects/hmg/invitations", json={"email": email, **extra}, headers=_h(owner))
    assert link.status_code == 200, link.text
    tok = link.json()["link"].rsplit("/", 1)[1]
    return c.post(f"/api/invitations/{tok}/accept", json={"name": email[:4], "password": "pass-word-1"}).json()["token"]


def test_a_member_limited_to_one_agent_sees_and_changes_only_that_agent(client):
    c, rt, store, loop = client
    owner = _setup(c)
    first = c.get("/api/agents", headers=_h(owner)).json()[0]["id"]
    second = c.post("/api/agents", json={"name": "Second agent"}, headers=_h(owner)).json()["id"]
    assert c.post("/api/projects/hmg/invitations", json={"email": "a@example.com", "agent_ids": ["nope"]},
                  headers=_h(owner)).status_code == 422
    dev = _join(c, owner, "dev@example.com", agent_ids=[second])
    assert [a["id"] for a in c.get("/api/agents", headers=_h(dev)).json()] == [second]
    assert c.get(f"/api/agents/{second}", headers=_h(dev)).status_code == 200
    assert c.get(f"/api/agents/{first}", headers=_h(dev)).status_code == 404
    assert c.put(f"/api/agents/{second}", json={"name": "Renamed", "description": ""}, headers=_h(dev)).status_code == 200
    assert c.put(f"/api/agents/{first}", json={"name": "x", "description": ""}, headers=_h(dev)).status_code == 404
    assert c.get(f"/api/calls?agent={first}", headers=_h(dev)).status_code == 404
    assert c.post("/api/agents", json={"name": "Third"}, headers=_h(dev)).status_code == 403          # no new agents
    assert c.get("/api/api-keys", headers=_h(dev)).status_code == 403
    assert c.post("/api/projects/hmg/invitations", json={"email": "x@example.com"}, headers=_h(dev)).status_code == 403
    members = c.get("/api/projects/hmg/members", headers=_h(owner)).json()
    uid = next(m["user_id"] for m in members["members"] if m["email"] == "dev@example.com")
    assert c.put(f"/api/projects/hmg/members/{uid}", json={"role": "admin", "agent_ids": None}, headers=_h(owner)).status_code == 200
    assert {a["id"] for a in c.get("/api/agents", headers=_h(dev)).json()} == {first, second}         # now all agents


def test_a_viewer_can_read_but_not_change(client):
    c, rt, store, loop = client
    owner = _setup(c)
    agent = c.get("/api/agents", headers=_h(owner)).json()[0]["id"]
    viewer = _join(c, owner, "view@example.com", role="viewer")
    assert c.get("/api/agents", headers=_h(viewer)).status_code == 200
    assert c.get(f"/api/agents/{agent}", headers=_h(viewer)).status_code == 200
    assert c.put(f"/api/agents/{agent}", json={"name": "x", "description": ""}, headers=_h(viewer)).status_code == 403
    assert c.post("/api/agents", json={"name": "New"}, headers=_h(viewer)).status_code == 403
    assert c.put("/api/me", json={"name": "Vee"}, headers=_h(viewer)).status_code == 200              # their own profile
    assert c.post("/api/projects/hmg/invitations", json={"email": "z@example.com"}, headers=_h(viewer)).status_code == 403


def test_invited_users_can_be_stopped_from_creating_projects(client):
    c, rt, store, loop = client
    owner = _setup(c)
    sara = _join(c, owner, "sara@example.com")
    rt.settings = rt.settings.model_copy(update={"invited_users_can_create_projects": False})
    assert c.post("/api/projects", json={"name": "Mine", "copy_setup": False}, headers=_h(sara)).status_code == 403
    assert c.post("/api/projects", json={"name": "Mine", "copy_setup": False}, headers=_h(owner)).status_code == 200
