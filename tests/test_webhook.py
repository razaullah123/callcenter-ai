"""Call webhook in Hamsa's format: validation, the five events and their envelope, order, retries, auth."""

import asyncio
import json
from types import SimpleNamespace

import httpx

from runtime.events import Event, EventType
from runtime.platform.bundle import validate
from runtime.platform.store import MemoryStore
from runtime.platform.webhook import EVENTS, WebhookSink, build_payload, ended_data, webhook_errors

URL = "https://hook.example/x"


def test_webhook_settings_are_validated():
    assert webhook_errors(None) == [] and webhook_errors({}) == []
    assert webhook_errors({"url": URL, "auth": {"secret": "T"}, "events": ["call.ended"]}) == []
    assert webhook_errors({"url": "http://x.example"}) and webhook_errors({"url": URL, "auth": "raw-token"})
    assert webhook_errors({"url": URL, "events": ["call.end"]})          # Hamsa's names are call.started / call.ended
    assert EVENTS == ("call.started", "call.answered", "transcription.update", "tool.executed", "call.ended")
    base = {"schema": 2, "models": {k: {"provider": "p"} for k in ("llm", "stt", "tts")}, "skill_files": {"a": {}}}
    assert validate(base) == [] and validate({**base, "webhook": {"url": "nope"}})


def test_envelope_and_ended_data_follow_hamsa():
    d = ended_data("c1", {"channel": "ivr", "mobile": "0501234567", "status": "completed"},
                   [{"role": "agent", "text": "Hello"}, {"role": "user", "text": "Hi"}, {"role": "system", "text": "handoff"}])
    assert d["transcription"] == [{"Agent": "Hello"}, {"User": "Hi"}] and d["conversationId"] == "c1"
    assert d["conversationRecording"] is None and d["outcomeResult"] == {} and d["call"] == {"channel": "ivr", "status": "completed"}
    p = build_payload("call.ended", "c1", d, agent_id="a1", agent_name="Agent One", project_id="hmg")
    assert {"eventType", "callId", "timestamp", "projectId", "agentId", "agentName", "data"} <= set(p)
    assert p["data"]["data"] is d and p["data"]["timestamp"] == p["timestamp"] and "0501234567" not in json.dumps(p)
    assert ended_data("c1", {}, [{"role": "user", "text": "x"}], include_transcript=False)["transcription"] == []


class Hook:
    """A sink wired to a MemoryStore release with a webhook, and a fake receiver."""

    def __init__(self, cfg, answers=(200,)):
        self.store, self.posts, self.answers = MemoryStore(), [], list(answers)
        self.emitted: list[Event] = []
        self.calls = {}
        self.rt = SimpleNamespace(platform=self.store, store=None, secrets=None, calls=self.calls,
                                  bus=SimpleNamespace(emit=lambda e: self.emitted.append(e)))
        self.sink = WebhookSink(self.rt, delays=(0, 0))

        def handler(req: httpx.Request) -> httpx.Response:
            self.posts.append({"auth": req.headers.get("authorization"), "body": json.loads(req.content), "headers": dict(req.headers), "raw": req.content})
            return httpx.Response(self.answers.pop(0) if len(self.answers) > 1 else self.answers[0])
        self.sink._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.cfg = cfg

    async def start(self, call_id="c1") -> int:
        await self.store.put_agent({"id": "a1", "name": "Agent One", "workspace_id": "hmg"})
        r = await self.store.add_release("a1", {"schema": 2, "webhook": self.cfg}, "t", "n")
        rid = r["id"] if isinstance(r, dict) else r
        await self.sink(Event(type=EventType.CALL_START, call_id=call_id, data={"agent_id": "a1", "release_id": rid}))
        return rid

    async def settle(self):
        while self.sink._tasks:
            await asyncio.gather(*list(self.sink._tasks))

    def types(self):
        return [p["body"]["eventType"] for p in self.posts]


async def test_a_call_start_sends_started_then_answered():
    h = Hook({"url": URL})
    await h.start()
    await h.settle()
    assert h.types() == ["call.started", "call.answered"]
    b = h.posts[0]["body"]
    assert (b["callId"], b["agentId"], b["agentName"], b["projectId"]) == ("c1", "a1", "Agent One", "hmg")


async def test_transcription_and_tool_events_arrive_in_order():
    h = Hook({"url": URL})
    await h.start()
    for e in (Event(type=EventType.TURN_START, call_id="c1", data={"text": "book me"}),
              Event(type=EventType.TOOL_START, call_id="c1", data={"tool": "get_doctors", "args": {"clinic": "x"}}),
              Event(type=EventType.TOOL_END, call_id="c1", latency_ms=120.0, data={"tool": "get_doctors"}),
              Event(type=EventType.AGENT_SAY, call_id="c1", data={"text": "Which day?"})):
        await h.sink(e)
    await h.settle()
    assert h.types() == ["call.started", "call.answered", "transcription.update", "tool.executed", "transcription.update"]
    say, tool = h.posts[2]["body"]["data"]["data"], h.posts[3]["body"]["data"]["data"]
    assert say == {"speaker": "User", "text": "book me"}
    assert (tool["toolName"], tool["input"], tool["success"], tool["duration"]) == ("get_doctors", {"clinic": "x"}, True, 120.0)


async def test_only_subscribed_events_are_sent():
    h = Hook({"url": URL, "events": ["call.ended"]})
    await h.start()
    await h.sink(Event(type=EventType.TURN_START, call_id="c1", data={"text": "hi"}))
    await h.settle()
    assert h.posts == []


async def test_call_ended_carries_the_conversation(monkeypatch):
    h = Hook({"url": URL})
    await h.start()

    async def get_call(call_id, scope=None):
        return {"call": {"agent_id": "a1", "status": "completed", "mobile": "0500000000"},
                "transcript": [{"role": "user", "text": "hello"}, {"role": "agent", "text": "hi"}]}
    monkeypatch.setattr("runtime.control.store.get_call", get_call)
    await h.sink(Event(type=EventType.CALL_END, call_id="c1", data={"reason": "agent_ended"}))
    await h.settle()
    last = h.posts[-1]["body"]
    assert last["eventType"] == "call.ended" and h.types()[-1] == "call.ended"
    assert last["data"]["data"]["transcription"] == [{"User": "hello"}, {"Agent": "hi"}]
    assert last["data"]["data"]["call"] == {"status": "completed"} and "0500000000" not in json.dumps(last)
    await h.sink(Event(type=EventType.AGENT_SAY, call_id="c1", data={"text": "late"}))      # after the end: ignored
    await h.settle()
    assert h.types()[-1] == "call.ended"


async def test_transcript_can_be_left_out(monkeypatch):
    h = Hook({"url": URL, "include_transcript": False})
    await h.start()

    async def get_call(call_id, scope=None):
        return {"call": {"agent_id": "a1"}, "transcript": [{"role": "user", "text": "x"}]}
    monkeypatch.setattr("runtime.control.store.get_call", get_call)
    await h.sink(Event(type=EventType.CALL_END, call_id="c1", data={}))
    await h.settle()
    assert h.posts[-1]["body"]["data"]["data"]["transcription"] == []


async def test_server_errors_are_retried_then_reported():
    h = Hook({"url": URL}, answers=(503,))
    assert await h.sink.deliver(h.cfg, {"eventType": "call.ended"}) == (False, "HTTP 503") and len(h.posts) == 3
    h2 = Hook({"url": URL}, answers=(500, 200))
    assert await h2.sink.deliver(h2.cfg, {"eventType": "call.ended"}) == (True, "HTTP 200") and len(h2.posts) == 2


async def test_a_refusal_is_not_retried_and_shows_as_a_warning():
    h = Hook({"url": URL, "events": ["call.started"]}, answers=(404,))
    await h.start()
    await h.settle()
    assert len(h.posts) == 1 and h.emitted[0].data["error"] == "HTTP 404" and h.emitted[0].call_id == "c1"


async def test_the_auth_secret_is_sent_as_bearer():
    h = Hook({"url": URL, "auth": {"secret": "WH"}})

    class Secrets:
        async def resolve(self, ref):
            return "s3cret"
    h.rt.secrets = Secrets()
    assert (await h.sink.deliver(h.cfg, {"eventType": "call.ended"}))[0] and h.posts[0]["auth"] == "Bearer s3cret"

    async def missing(ref):
        raise LookupError("secret WH is not set")
    h.rt.secrets = SimpleNamespace(resolve=missing)
    assert await h.sink.deliver(h.cfg, {"eventType": "call.ended"}) == (False, "secret WH is not set")


# ---------------------------------------------------------------- params echo, signing, ids, history

def _live(h, call_id="c1", **params):
    h.calls[call_id] = SimpleNamespace(session=SimpleNamespace(params=params))


async def test_custom_parameters_are_echoed_back(monkeypatch):
    h = Hook({"url": URL})
    _live(h, customer_name="Sara", order="A-1")
    await h.start()
    await h.settle()
    assert h.posts[0]["body"]["data"]["data"]["params"] == {"customer_name": "Sara", "order": "A-1"}      # call.started

    async def get_call(call_id, scope=None):
        return {"call": {"agent_id": "a1"}, "transcript": []}
    monkeypatch.setattr("runtime.control.store.get_call", get_call)
    await h.sink(Event(type=EventType.CALL_END, call_id="c1", data={}))
    await h.settle()
    assert h.posts[-1]["body"]["data"]["data"]["outcomeResult"] == {"customer_name": "Sara", "order": "A-1"}
    h2 = Hook({"url": URL})                                                   # a call without params: an empty object
    await h2.start("c2")
    await h2.settle()
    assert h2.posts[0]["body"]["data"]["data"]["params"] == {}


async def test_requests_are_signed_and_carry_an_id_that_survives_retries():
    from runtime.platform.webhook import sign
    h = Hook({"url": URL}, answers=(503, 200))
    ok, detail = await h.sink.deliver(h.cfg, {"eventType": "call.ended", "callId": "c1"}, signing_secret="s3cret")
    assert ok and len(h.posts) == 2
    a, b = h.posts
    assert a["headers"]["x-webhook-id"] == b["headers"]["x-webhook-id"] and a["headers"]["x-webhook-event"] == "call.ended"
    for p in (a, b):
        assert p["headers"]["x-webhook-signature"] == sign("s3cret", p["headers"]["x-webhook-timestamp"], p["raw"])
        assert p["headers"]["x-webhook-signature"].startswith("sha256=") and len(p["headers"]["x-webhook-signature"]) == 71
    h2 = Hook({"url": URL})                                                   # no secret: no signature header
    await h2.sink.deliver(h2.cfg, {"eventType": "call.ended"})
    assert "x-webhook-signature" not in h2.posts[0]["headers"] and "x-webhook-id" in h2.posts[0]["headers"]
    other = Hook({"url": URL})
    await other.sink.deliver(other.cfg, {"eventType": "call.ended"})
    assert other.posts[0]["headers"]["x-webhook-id"] != h2.posts[0]["headers"]["x-webhook-id"]


async def test_the_signing_secret_comes_from_the_secret_store():
    from runtime.platform.webhook import sign

    class Secrets:
        async def resolve(self, ref):
            return {"WH_SIGN": "stored-secret", "WH_TOKEN": "tok"}[ref["secret"]]
    h = Hook({"url": URL, "auth": {"secret": "WH_TOKEN"}, "signing": {"secret": "WH_SIGN"}})
    h.rt.secrets = Secrets()
    await h.sink.deliver(h.cfg, {"eventType": "call.started"})
    p = h.posts[0]
    assert p["auth"] == "Bearer tok" and p["headers"]["x-webhook-signature"] == sign("stored-secret", p["headers"]["x-webhook-timestamp"], p["raw"])
    assert webhook_errors({"url": URL, "signing": {"secret": "X"}}) == [] and webhook_errors({"url": URL, "signing": "x"})


async def test_every_delivery_goes_into_the_history(monkeypatch):
    h = Hook({"url": URL}, answers=(200,))
    await h.start()
    await h.sink(Event(type=EventType.TURN_START, call_id="c1", data={"text": "hi"}))
    await h.settle()
    rows = await h.store.webhook_deliveries("a1")
    assert [(r["event"], r["ok"], r["attempts"]) for r in rows] == [("transcription.update", True, 1), ("call.answered", True, 1), ("call.started", True, 1)]
    assert rows[0]["call_id"] == "c1" and rows[0]["detail"] == "HTTP 200" and rows[0]["workspace_id"] == "hmg"
    bad = Hook({"url": URL}, answers=(503,))
    await bad.start()
    await bad.settle()
    first = (await bad.store.webhook_deliveries("a1"))[-1]
    assert (first["ok"], first["attempts"], first["detail"]) == (False, 3, "HTTP 503")
    refused = Hook({"url": URL}, answers=(400,))
    await refused.start()
    await refused.settle()
    assert (await refused.store.webhook_deliveries("a1"))[0]["attempts"] == 1            # a refusal isn't retried


async def test_history_keeps_the_latest_200_per_agent():
    store = MemoryStore()
    for i in range(205):
        await store.add_webhook_delivery({"agent_id": "a1", "workspace_id": "hmg", "call_id": f"c{i}", "event": "call.ended", "ok": True})
    await store.add_webhook_delivery({"agent_id": "a2", "workspace_id": "hmg", "call_id": "x", "event": "call.ended", "ok": True})
    rows = await store.webhook_deliveries("a1", limit=500)
    assert len(rows) == 200 and rows[0]["call_id"] == "c204" and rows[-1]["call_id"] == "c5"
    assert len(await store.webhook_deliveries("a2")) == 1 and len(await store.webhook_deliveries("a1", limit=10)) == 10


def test_api_test_delivery_and_history(monkeypatch):
    import asyncio

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from runtime.config import get_settings
    from runtime.control import agents_api
    from runtime.platform import webhook
    from runtime.platform.seed import seed
    from runtime.server import app as server_app

    from .test_studio import _Rt
    loop = asyncio.new_event_loop()
    store = MemoryStore()
    loop.run_until_complete(seed(store, get_settings()))
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append({"auth": req.headers.get("authorization"), "sig": req.headers.get("x-webhook-signature"), "body": json.loads(req.content)})
        return httpx.Response(200)
    monkeypatch.setattr(webhook.WebhookSink, "_http", lambda self: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    rt = _Rt(store, None, None, None)
    monkeypatch.setitem(server_app.state, "rt", rt)
    app = FastAPI()
    app.include_router(agents_api.router)
    with TestClient(app) as c:
        agent = next(a["id"] for a in c.get("/api/agents").json())
        assert c.get(f"/api/agents/{agent}/webhook/deliveries").json() == []
        r = c.post(f"/api/agents/{agent}/webhook/test", json={"webhook": {"url": URL}, "token": "typed-not-saved", "signing_secret": "sig-secret"})
        assert r.status_code == 200 and r.json() == {"ok": True, "detail": "HTTP 200"}
        assert seen[0]["auth"] == "Bearer typed-not-saved" and seen[0]["sig"].startswith("sha256=")       # no saved secret needed
        rows = c.get(f"/api/agents/{agent}/webhook/deliveries").json()
        assert len(rows) == 1 and rows[0]["event"] == "call.ended" and rows[0]["ok"] and rows[0]["detail"] == "test: HTTP 200"
        assert c.get("/api/agents/nope/webhook/deliveries").status_code == 404
