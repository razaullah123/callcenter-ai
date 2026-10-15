"""Publishing (Hamsa's "Publish"): the public page / embed widget of an agent — settings, console API, public routes, limits."""

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.websockets import WebSocketDisconnect

from runtime.config import Settings, get_settings
from runtime.control.config_store import ProviderSet
from runtime.events import EventBus
from runtime.platform.loader import AgentLoader, LoadedAgent
from runtime.platform.seed import seed
from runtime.platform.share import LIMITER, REFUSALS, LimitSettings, PublicLimiter, ShareSettings, new_token, public_config
from runtime.platform.store import MemoryStore
from runtime.skills import FileSkillSet
from runtime.tools import MockMCP
from runtime.tools.factory import build_tooling

from .test_harness import ScriptedLLM
from .test_ivr import FakeSTT, FakeTTS, LoudVAD
from .test_studio import _Rt


# ---------------------------------------------------------------- settings and limits


def test_defaults_are_strict_and_valid():
    s = ShareSettings()
    assert s.limits.max_minutes == 5 and s.limits.max_concurrent == 5 and s.limits.per_ip_per_hour == 10
    assert s.limits.allowed_origins == [] and s.theme == "dark" and s.visualizer == "orb" and not s.show_name and s.show_transcript
    assert s.embed.position == "bottom-right" and s.embed.size == "md" and s.embed.launcher == "auto" and not s.embed.auto_start


@pytest.mark.parametrize("patch", [
    {"gradient": ["#fff", "#000000"]}, {"gradient": ["#CADCFC"]}, {"bg_dark": "black"}, {"theme": "blue"}, {"visualizer": "bars"},
    {"description": "x" * 61}, {"embed": {"position": "top"}}, {"embed": {"color": "red"}}, {"embed": {"label": "x" * 41}},
    {"limits": {"max_minutes": 0}}, {"limits": {"max_minutes": 61}}, {"limits": {"max_concurrent": 0}}, {"limits": {"per_ip_per_hour": 0}},
    {"limits": {"allowed_origins": ["example.com"]}}, {"limits": {"allowed_origins": ["https://a.com/path"]}},
    {"limits": {"allowed_params": ["Bad Name"]}}, {"limits": {"allowed_params": ["call_id"]}}, {"colour": "red"}])
def test_bad_settings_are_refused(patch):
    with pytest.raises(ValidationError):
        ShareSettings.model_validate(patch)


def test_good_settings_are_normalised():
    s = ShareSettings.model_validate({"limits": {"allowed_origins": [" https://shop.example.com/ ", "http://localhost:3000"],
                                                 "allowed_params": ["customer_name"], "max_minutes": 2.5}})
    assert s.limits.allowed_origins == ["https://shop.example.com", "http://localhost:3000"] and s.limits.max_minutes == 2.5


def test_public_config_shows_only_what_the_page_needs():
    cfg = public_config({"show_name": False, "description": "Hello", "limits": {"max_minutes": 3, "allowed_params": ["a_b"]}},
                        {"name": "Internal Name"})
    assert cfg["name"] == "" and cfg["description"] == "Hello" and cfg["allowed_params"] == ["a_b"] and cfg["max_minutes"] == 3
    assert "limits" not in cfg and "allowed_origins" not in json.dumps(cfg)
    assert public_config({"show_name": True}, {"name": "Internal Name"})["name"] == "Internal Name"
    assert len(new_token()) >= 32 and new_token() != new_token()


def test_limiter_counts_calls_at_once_and_per_visitor():
    lim = PublicLimiter()
    limits = LimitSettings(max_concurrent=2, per_ip_per_hour=3)
    a, b = object(), object()
    assert lim.admit("t", "1.1.1.1", limits, now=0) is None
    h1 = lim.join("t", a)
    assert lim.admit("t", "2.2.2.2", limits, now=1) is None
    lim.join("t", b)
    assert lim.admit("t", "3.3.3.3", limits, now=2) == "busy"                   # two calls already running
    lim.leave("t", h1)
    assert lim.admit("t", "1.1.1.1", limits, now=3) is None                      # a second one from the first visitor
    assert lim.admit("t", "1.1.1.1", limits, now=4) is None                      # (the third; the line was free again)
    lim.leave("t", 2)
    assert lim.admit("t", "1.1.1.1", limits, now=5) == "rate"                    # a 4th within the hour
    assert lim.admit("t", "1.1.1.1", limits, now=3700) is None                   # an hour later it is fine again
    assert lim.admit("other", "1.1.1.1", limits, now=5) is None                  # another link counts separately
    assert set(REFUSALS) == {"busy", "rate"}


# ---------------------------------------------------------------- the console API


@pytest.fixture
def console(monkeypatch):
    from runtime.control import agents_api, api as control_api, share_api
    from runtime.server import app as server_app
    loop = asyncio.new_event_loop()
    store = MemoryStore()

    async def setup():
        await seed(store, get_settings())
        mcp = MockMCP()
        await mcp.start()
        loader = AgentLoader(store, mcp, get_settings(), warm_phrases=False)
        return _Rt(store, mcp, loader, await loader.for_call())
    rt = loop.run_until_complete(setup())
    monkeypatch.setitem(server_app.state, "rt", rt)
    app = FastAPI()
    for r in (agents_api.router, control_api.router, share_api.router):
        app.include_router(r)
    with TestClient(app) as c:
        yield c, rt, store, loop
    loop.close()


def test_share_api_publish_update_unpublish(console):
    c, rt, store, loop = console
    r = c.get("/api/agents/hmg-care/share").json()
    assert r["published"] is False and r["token"] is None and r["settings"] == r["defaults"]
    assert c.get("/api/agents/nope/share").status_code == 404
    r = c.put("/api/agents/hmg-care/share", json={"settings": {"tagline": "Tap to start", "theme": "light"}})
    assert r.status_code == 200, r.text
    first = r.json()
    assert first["published"] and first["path"] == f"/p/{first['token']}" and first["settings"]["tagline"] == "Tap to start"
    assert first["settings"]["limits"]["max_minutes"] == 5 and first["settings"]["theme"] == "light"
    second = c.put("/api/agents/hmg-care/share", json={"settings": {"show_name": True}}).json()
    assert second["token"] == first["token"] and second["settings"]["show_name"] is True and second["settings"]["tagline"] == ""
    assert c.get("/api/agents/hmg-care/share").json()["token"] == first["token"]
    assert c.delete("/api/agents/hmg-care/share").json() == {"published": False, "ended_calls": 0}
    assert c.get("/api/agents/hmg-care/share").json()["published"] is False
    again = c.put("/api/agents/hmg-care/share", json={"settings": {}}).json()
    assert again["token"] != first["token"]                                       # an unpublished link never comes back
    actions = [a["action"] for a in loop.run_until_complete(store.audit_log("hmg-care"))]
    assert "share.published" in actions and "share.unpublished" in actions


def test_share_api_validates_and_needs_a_release(console):
    c, rt, store, loop = console
    r = c.put("/api/agents/hmg-care/share", json={"settings": {"limits": {"max_minutes": 0}, "embed": {"color": "red"}}})
    assert r.status_code == 422 and "max_minutes" in r.json()["detail"] and "color" in r.json()["detail"]
    c.post("/api/agents", json={"name": "Brand New"})
    loop.run_until_complete(store.put_agent({"id": "no-release", "workspace_id": "hmg", "name": "No Release", "description": ""}))
    r = c.put("/api/agents/no-release/share", json={"settings": {}})
    assert r.status_code == 409 and "published" in r.json()["detail"]


def test_unpublishing_ends_the_calls_on_the_link(console):
    c, rt, store, loop = console
    token = c.put("/api/agents/hmg-care/share", json={"settings": {}}).json()["token"]
    ended = []

    class FakeCall:
        async def end_from_console(self):
            ended.append(True)

    handle = LIMITER.join(token, FakeCall())
    try:
        assert c.delete("/api/agents/hmg-care/share").json()["ended_calls"] == 1 and ended == [True]
    finally:
        LIMITER.leave(token, handle)


def test_deleting_an_agent_removes_its_link(console):
    c, rt, store, loop = console
    c.post("/api/agents", json={"name": "Temp Agent"})
    loop.run_until_complete(store.put_share({"agent_id": "temp-agent", "workspace_id": "hmg", "token": "tok-x", "settings": {}}))
    assert loop.run_until_complete(store.share_by_token("tok-x")) is not None
    loop.run_until_complete(store.delete_agent("temp-agent"))
    assert loop.run_until_complete(store.share_by_token("tok-x")) is None


# ---------------------------------------------------------------- the public routes


class PublicRuntime:
    def __init__(self, settings, bus, store, loaded):
        self.settings, self.bus, self.platform, self.calls, self._loaded = settings, bus, store, {}, loaded
        self.asked = []

    async def agent_for_call(self, *, agent_id=None, number=None, draft=False):
        self.asked.append((agent_id, draft))
        return self._loaded


@pytest.fixture
def public(monkeypatch):
    from runtime.server import app as server_app
    from runtime.server import public as public_routes
    from runtime.voice import turn
    monkeypatch.setattr(turn, "SileroVAD", LoudVAD)
    loop = asyncio.new_event_loop()
    executor = loop.run_until_complete(build_tooling(MockMCP()))
    settings = Settings(database_url=None, voice_end_silence_ms=300)
    loaded = LoadedAgent.from_parts(settings=settings, providers=ProviderSet(0, {}, ScriptedLLM(), FakeSTT(), FakeTTS()),
                                    skills=FileSkillSet(executor.catalog), executor=executor)
    loaded.agent_id, loaded.name = "line", "Line Agent"
    store = MemoryStore()
    loop.run_until_complete(store.put_agent({"id": "line", "workspace_id": "hmg", "name": "Line Agent", "description": ""}))
    rt = PublicRuntime(settings, EventBus(), store, loaded)
    monkeypatch.setitem(server_app.state, "rt", rt)
    monkeypatch.setitem(server_app.state, "phrases", None)
    LIMITER.active.clear()
    LIMITER.hits.clear()
    app = FastAPI()
    app.include_router(public_routes.router)

    def publish(**settings_patch):
        token = "tok-" + new_token()[:10]
        loop.run_until_complete(store.put_share({"agent_id": "line", "workspace_id": "hmg", "token": token, "settings": settings_patch}))
        return token
    with TestClient(app) as c:
        yield c, rt, publish, loop
    LIMITER.active.clear()
    LIMITER.hits.clear()
    loop.close()


def test_public_page_and_unknown_links(public):
    c, rt, publish, loop = public
    token = publish(tagline="Hello")
    page = c.get(f"/p/{token}")
    assert page.status_code == 200 and f'const TOKEN = "{token}"' in page.text and "/ws/public/" in page.text
    assert page.headers["cache-control"] == "no-store" and "content-security-policy" not in page.headers      # any site may frame it
    assert c.get("/p/not-a-token").status_code == 404 and "no longer available" in c.get("/p/not-a-token").text
    preview = c.get("/p/preview")
    assert preview.status_code == 200 and preview.headers["content-security-policy"] == "frame-ancestors 'self'"
    assert 'const TOKEN = "preview"' in preview.text


def test_page_can_only_be_framed_by_the_allowed_sites(public):
    c, rt, publish, loop = public
    token = publish(limits={"allowed_origins": ["https://shop.example.com", "http://localhost:3000"]})
    csp = c.get(f"/p/{token}").headers["content-security-policy"]
    assert csp == "frame-ancestors 'self' https://shop.example.com http://localhost:3000"


def test_config_and_embed_script(public):
    c, rt, publish, loop = public
    token = publish(description="Ask us anything", show_name=True, embed={"label": "Talk to us", "color": "#112233"},
                    limits={"allowed_params": ["customer_name"], "allowed_origins": ["https://a.com"]})
    r = c.get(f"/public/{token}/config")
    assert r.status_code == 200 and r.headers["access-control-allow-origin"] == "*"
    cfg = r.json()
    assert cfg["name"] == "Line Agent" and cfg["description"] == "Ask us anything" and cfg["embed"]["label"] == "Talk to us"
    assert cfg["allowed_params"] == ["customer_name"] and "allowed_origins" not in json.dumps(cfg)
    assert c.get("/public/nope/config").status_code == 404
    js = c.get("/embed.js")
    assert js.status_code == 200 and js.headers["content-type"].startswith("application/javascript")
    assert "data-token" in js.text and "data-agent-trigger" in js.text and js.headers["access-control-allow-origin"] == "*"


def _refusal(c, token, headers=None):
    """The error message a refused socket sends (then it closes)."""
    with c.websocket_connect(f"/ws/public/{token}", headers=headers or {}) as ws:
        msg = json.loads(ws.receive_text())
        assert msg["event"] == "error"
        with pytest.raises(WebSocketDisconnect):
            ws.receive_text()
        return msg["message"]


def test_unknown_link_and_foreign_site_are_refused(public):
    c, rt, publish, loop = public
    assert "no longer available" in _refusal(c, "nope")
    token = publish()
    assert "can't open a call from here" in _refusal(c, token, headers={"origin": "https://evil.example.com"})
    assert rt.asked == []                                                         # nothing was started for either


def test_a_call_starts_the_published_agent_and_ends_cleanly(public):
    c, rt, publish, loop = public
    token = publish(limits={"max_minutes": 2, "allowed_params": ["customer_name"]})
    with c.websocket_connect(f"/ws/public/{token}", headers={"origin": "http://testserver"}) as ws:
        ws.send_text(json.dumps({"event": "start", "audio": {"encoding": "pcm16", "sample_rate": 16000},
                                 "agent": "someone-else", "draft": True, "call_id": "mine",
                                 "params": {"customer_name": "Sara", "business_name": "x"}}))
        ready = json.loads(ws.receive_text())
        assert ready["event"] == "ready" and ready["call_id"].startswith("pub-")
        greeting = json.loads(ws.receive_text())
        assert greeting["event"] == "transcript" and greeting["role"] == "agent"
        call = rt.calls[ready["call_id"]]
        assert call.max_call_s == 120                                             # the link's limit caps the call
        assert call.session.params == {"customer_name": "Sara"}                    # only what the owner allowed
        assert len(LIMITER.calls_of(token)) == 1
        ws.send_text(json.dumps({"event": "stop"}))
    assert rt.asked == [("line", False)]                                          # the visitor can't pick another agent or the draft
    assert rt.calls == {} and LIMITER.calls_of(token) == []


START = json.dumps({"event": "start", "audio": {"encoding": "pcm16", "sample_rate": 16000}})


def test_calls_at_once_are_limited(public):
    c, rt, publish, loop = public
    token = publish(limits={"max_concurrent": 1})
    with c.websocket_connect(f"/ws/public/{token}") as first:
        first.send_text(START)
        assert json.loads(first.receive_text())["event"] == "ready"
        assert _refusal(c, token) == REFUSALS["busy"]                              # the only line is taken
        first.send_text(json.dumps({"event": "stop"}))
    with c.websocket_connect(f"/ws/public/{token}") as again:                      # free again
        again.send_text(START)
        assert json.loads(again.receive_text())["event"] == "ready"
        again.send_text(json.dumps({"event": "stop"}))


def test_calls_per_visitor_per_hour_are_limited(public):
    c, rt, publish, loop = public
    token = publish(limits={"per_ip_per_hour": 2})
    for _ in range(2):
        with c.websocket_connect(f"/ws/public/{token}") as ws:
            ws.send_text(START)
            assert json.loads(ws.receive_text())["event"] == "ready"
            ws.send_text(json.dumps({"event": "stop"}))
    assert _refusal(c, token) == REFUSALS["rate"]                                  # the 3rd call within the hour


def test_bad_start_is_refused(public):
    c, rt, publish, loop = public
    token = publish()
    with c.websocket_connect(f"/ws/public/{token}") as ws:
        ws.send_text(json.dumps({"event": "hello"}))
        assert json.loads(ws.receive_text())["event"] == "error"
