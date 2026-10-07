"""API keys (Hamsa parity): creating, using and revoking project keys; what a key may and may not do."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from runtime.control import accounts as acc
from runtime.control import api as control_api
from runtime.control import keys_api
from runtime.platform.store import MemoryStore

from .test_projects import console  # noqa: F401 — the fixture


@pytest.fixture
def api(console, monkeypatch):  # noqa: F811
    c, rt, store, loop = console
    from runtime.control import accounts_api
    c.app.include_router(keys_api.router)
    c.app.include_router(accounts_api.router)
    control_api._RATE.clear()
    return c, rt, store, loop


def make_key(c, **body) -> dict:
    r = c.post("/api/api-keys", json={"name": "CRM", "scope": "full", **body})
    assert r.status_code == 200, r.text
    return r.json()


def token(key) -> dict:
    return {"Authorization": f"Token {key['key']}"}


# ---------------------------------------------------------------- the key itself

def test_a_key_is_shown_once_and_only_its_hash_is_stored(api):
    c, rt, store, loop = api
    k = make_key(c)
    assert k["key"].startswith("hmg_") and len(k["key"]) > 40 and k["prefix"] == k["key"][:12] and "not shown again" in k["note"]
    listed = c.get("/api/api-keys").json()
    assert [x["id"] for x in listed] == [k["id"]] and "key" not in listed[0] and "key_hash" not in listed[0]
    assert listed[0]["status"] == "active" and listed[0]["scope"] == "full" and listed[0]["last_used_at"] is None
    stored = loop.run_until_complete(store.api_key_by_hash(acc.token_hash(k["key"])))
    assert stored["id"] == k["id"] and k["key"] not in str(stored)                   # the hash, never the key
    assert loop.run_until_complete(store.api_key_by_hash(acc.token_hash("hmg_wrong"))) is None


def test_creation_rules(api):
    c, rt, store, loop = api
    assert c.post("/api/api-keys", json={"name": "x", "scope": "root"}).status_code == 422
    assert c.post("/api/api-keys", json={"name": "   "}).status_code == 422
    assert c.post("/api/api-keys", json={"name": "x" * 61}).status_code == 422
    assert c.post("/api/api-keys", json={"name": "x", "expires_days": 0}).status_code == 422
    exp = make_key(c, expires_days=30)
    assert timedelta(days=29) < datetime.fromisoformat(str(exp["expires_at"]).replace("Z", "+00:00")) - datetime.now(timezone.utc) < timedelta(days=31) \
        if isinstance(exp["expires_at"], str) else timedelta(days=29) < exp["expires_at"] - datetime.now(timezone.utc) < timedelta(days=31)
    for i in range(keys_api.MAX_ACTIVE - 1):
        make_key(c, name=f"k{i}")
    assert c.post("/api/api-keys", json={"name": "one too many"}).status_code == 409
    first = c.get("/api/api-keys").json()[-1]["id"]
    assert c.delete(f"/api/api-keys/{first}").status_code == 200                         # a revoked key frees a place
    assert c.post("/api/api-keys", json={"name": "now fine"}).status_code == 200


def test_resolving_keys():
    store = MemoryStore()
    loop = asyncio.new_event_loop()
    now = datetime.now(timezone.utc)

    def add(raw, **kw):
        loop.run_until_complete(store.create_api_key({"id": raw, "workspace_id": "hmg", "name": "CRM", "prefix": raw[:12],
                                                      "key_hash": acc.token_hash(raw), "scope": "full", **kw}))
    add("hmg_good_key_000001")
    add("hmg_revoked_key_0002")
    loop.run_until_complete(store.revoke_api_key("hmg", "hmg_revoked_key_0002"))
    add("hmg_expired_key_0003", expires_at=now - timedelta(minutes=1))
    resolve = lambda raw: loop.run_until_complete(acc.resolve(store, SimpleSettings(), raw))        # noqa: E731
    who = resolve("hmg_good_key_000001")
    assert who.kind == "key" and not who.is_user and who.role("hmg") == "admin" and who.role("other") is None
    assert who.actor.startswith("API key CRM (hmg_good_key")
    assert resolve("hmg_revoked_key_0002") is None and resolve("hmg_expired_key_0003") is None
    assert resolve("hmg_never_issued") is None            # with no accounts and no token the console is open — a key still never is
    assert resolve(None).kind == "open"
    used = loop.run_until_complete(store.api_key_by_hash(acc.token_hash("hmg_good_key_000001")))["last_used_at"]
    assert used is not None
    resolve("hmg_good_key_000001")                        # used again within 5 minutes: not written again
    assert loop.run_until_complete(store.api_key_by_hash(acc.token_hash("hmg_good_key_000001")))["last_used_at"] == used
    loop.close()


class SimpleSettings:
    console_token = None


# ---------------------------------------------------------------- using a key

def test_all_three_header_forms_work_and_bad_keys_are_refused(api):
    c, rt, store, loop = api
    k = make_key(c)
    assert c.get("/api/agents", headers={"Authorization": f"Token {k['key']}"}).status_code == 200
    assert c.get("/api/agents", headers={"Authorization": f"Bearer {k['key']}"}).status_code == 200
    assert c.get("/api/agents", headers={"X-API-Key": k["key"]}).status_code == 200
    bad = c.get("/api/agents", headers={"Authorization": "Token hmg_not_a_real_key"})
    assert bad.status_code == 401 and "not valid" in bad.json()["detail"]["message"]
    assert c.get("/api/agents", headers={"X-API-Key": "hmg_not_a_real_key"}).status_code == 401
    me = c.get("/api/whoami", headers=token(k)).json()
    assert me["kind"] == "key" and me["project"]["id"] == "hmg" and me["key"] == {"id": k["id"], "name": "CRM", "prefix": k["prefix"], "scope": "full"}
    assert c.get("/api/whoami").json()["kind"] == "open" and "key" not in c.get("/api/whoami").json()


def test_revoking_and_expiry_end_a_key_at_once(api):
    c, rt, store, loop = api
    k = make_key(c)
    assert c.get("/api/agents", headers=token(k)).status_code == 200
    assert c.delete(f"/api/api-keys/{k['id']}").json()["revoked"] is True
    assert c.get("/api/agents", headers=token(k)).status_code == 401
    assert c.delete(f"/api/api-keys/{k['id']}").status_code == 404                        # already revoked
    assert c.get("/api/api-keys").json()[0]["status"] == "revoked"
    e = make_key(c, name="short")
    loop.run_until_complete(store.create_api_key({"id": "old", "workspace_id": "hmg", "name": "old", "prefix": "hmg_oldkey00", "scope": "full",
                                                  "key_hash": acc.token_hash("hmg_oldkey00_expired"), "expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)}))
    assert c.get("/api/agents", headers={"Authorization": "Token hmg_oldkey00_expired"}).status_code == 401
    assert next(x for x in c.get("/api/api-keys").json() if x["id"] == "old")["status"] == "expired" and e


def test_a_read_only_key_can_only_read(api):
    c, rt, store, loop = api
    k = make_key(c, name="dashboard", scope="read")
    assert c.get("/api/agents", headers=token(k)).status_code == 200
    assert c.get("/api/calls", headers=token(k)).status_code in (200, 500)               # reading the call history is allowed (needs a database)
    denied = c.put("/api/routes", headers=token(k), json={"pattern": "+966112345678", "agent_id": "hmg-care"})
    assert denied.status_code == 403 and "read-only" in denied.json()["detail"]["message"]
    full = make_key(c, name="writer", scope="full")
    ok = c.put("/api/routes", headers=token(full), json={"pattern": "+966112345678", "agent_id": "hmg-care", "label": "via key"})
    assert ok.status_code == 200, ok.text


def test_a_key_never_manages_keys_people_projects_or_secrets(api):
    c, rt, store, loop = api
    k = make_key(c)
    h = token(k)
    for method, path, body in (("get", "/api/api-keys", None), ("post", "/api/api-keys", {"name": "mine"}), ("delete", f"/api/api-keys/{k['id']}", None),
                               ("get", "/api/me", None), ("put", "/api/me", {"name": "x"}),
                               ("post", "/api/projects", {"name": "Another"}), ("put", "/api/projects/hmg", {"name": "x"}),
                               ("get", "/api/projects/hmg/members", None), ("post", "/api/projects/hmg/invitations", {"email": "a@b.c"}),
                               ("put", "/api/secrets/MY_SECRET", {"value": "v"}), ("delete", "/api/secrets/MY_SECRET", None)):
        r = getattr(c, method)(path, headers=h, **({"json": body} if body is not None else {}))
        assert r.status_code == 403, (method, path, r.status_code)
        assert "can't manage keys, people, projects or secrets" in r.json()["detail"]["message"]
    assert c.get("/api/secrets", headers=h).status_code != 403                           # listing (masked) is read-only: the guard lets it through
    assert c.get("/api/projects", headers=h).status_code == 200
    assert c.get("/api/api-keys").status_code == 200                                      # the console itself still can


def test_a_key_stays_in_its_project(api):
    c, rt, store, loop = api
    assert c.post("/api/projects", json={"name": "Clinic"}).status_code == 200
    k = make_key(c)                                                                      # made in the default project
    projects = c.get("/api/projects", headers=token(k)).json()
    assert [p["id"] for p in projects] == ["hmg"]                                        # no other project is even listed
    other = c.get("/api/agents", headers={**token(k), "X-Project": "clinic"})
    assert other.status_code == 403 and "belongs to project 'hmg'" in other.json()["detail"]["message"]
    assert c.get("/api/agents", headers={**token(k), "X-Project": "hmg"}).status_code == 200
    assert all(a["id"] for a in c.get("/api/agents", headers=token(k)).json())
    assert c.get("/api/agents", headers={"X-Project": "clinic"}).status_code == 200      # the console can


def test_a_keys_changes_are_audited_under_its_name(api):
    c, rt, store, loop = api
    k = make_key(c, name="CRM sync")
    assert c.put("/api/routes", headers=token(k), json={"pattern": "+966119999999", "agent_id": "hmg-care"}).status_code == 200
    log = loop.run_until_complete(store.project_audit_log("hmg"))
    assert any(a["action"] == "route.set" and a["actor"].startswith("API key CRM sync (hmg_") for a in log)
    assert any(a["action"] == "apikey.created" for a in log)
    assert k["key"] not in str(log)                                                      # the key never reaches the log


def test_keys_are_rate_limited_each_on_its_own(api):
    c, rt, store, loop = api
    rt.settings = rt.settings.model_copy(update={"api_key_rate_per_min": 3})
    a, b = make_key(c, name="a"), make_key(c, name="b")
    assert [c.get("/api/whoami", headers=token(a)).status_code for _ in range(3)] == [200, 200, 200]
    limited = c.get("/api/whoami", headers=token(a))
    assert limited.status_code == 429 and 1 <= int(limited.headers["Retry-After"]) <= 60 and "3 a minute" in limited.json()["detail"]["message"]
    assert c.get("/api/whoami", headers=token(b)).status_code == 200                      # another key is not affected
    assert c.get("/api/whoami").status_code == 200                                        # nor is the console


def test_a_key_cannot_listen_in_on_calls(api):
    c, rt, store, loop = api
    k = make_key(c)
    with pytest.raises(Exception):
        with c.websocket_connect(f"/api/live/listen?call_id=x&token={k['key']}") as ws:
            ws.receive_text()
