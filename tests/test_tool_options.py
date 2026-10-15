"""Hamsa parity (Tools): token auth, async tools, active / inactive tools, start / done messages."""

import asyncio

import httpx
import pytest

from runtime.events import EventBus
from runtime.platform import toollib
from runtime.providers import ToolCall
from runtime.tools import ToolContext, ToolDef, http_tool
from runtime.tools.catalog import Catalog
from runtime.tools.http_tool import auth_errors, auth_headers

from .test_flow_options import make_agent

SCHEMA = {"type": "object", "properties": {"order": {"type": "string"}}, "required": ["order"]}


def http_def(name="notify", **kw) -> ToolDef:
    base = dict(skill="line", kind="write", source="http", description="Notify the warehouse", input_schema=SCHEMA,
                confirm="none", http={"method": "POST", "url": "https://api.example.com/notify"})
    return ToolDef(name=name, **{**base, **kw})


def validate(policy, name="t"):
    return toollib.validate_policy(name, policy, mcp_tools={name}, local_tools=set())


def http_policy(**kw):
    return {"kind": "write", "source": "http", "description": "Notify", "input_schema": SCHEMA,
            "http": {"method": "POST", "url": "https://api.example.com/notify"}, **kw}


class Backend:
    """A fake HTTP API: records the calls, optionally waits before answering."""

    def __init__(self, monkeypatch, delay=0.0):
        self.calls, self.delay, self.gate = [], delay, asyncio.Event()

        async def handler(request: httpx.Request) -> httpx.Response:
            self.calls.append((str(request.url), request.headers.get("authorization")))
            if self.delay:
                await asyncio.sleep(self.delay)
            return httpx.Response(200, json={"success": True})
        monkeypatch.setattr(http_tool, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        monkeypatch.setattr(http_tool, "simulated", lambda kind: None)


def run(agent, name="notify", **args):
    ctx = agent._ctx()
    ctx.allowed_tools = None
    return agent._execute_with_filler(ToolCall(id="1", name=name, arguments=args or {"order": "A1"}), ctx, EventBus().bind())


# ---------------------------------------------------------------- token auth


def test_token_auth_sends_authorization_token():
    assert auth_headers({"type": "token", "token": "abc"}) == {"Authorization": "Token abc"}
    assert auth_errors({"type": "token", "token": "abc"}) == []
    assert auth_errors({"type": "token"}) == ["http.auth.token is required for token"]
    assert validate(http_policy(http={"method": "POST", "url": "https://x.example/n", "auth": {"type": "token", "token": "k"}})) == []


# ---------------------------------------------------------------- policy fields


def test_new_policy_fields_are_validated():
    assert validate(http_policy(enabled=False, **{"async": True})) == []
    assert any("enabled" in e for e in validate(http_policy(enabled="no")))
    assert any("async" in e for e in validate(http_policy(**{"async": 1})))
    assert any("cannot be async" in e for e in validate({**http_policy(), "kind": "read", "async": True}))
    assert validate(http_policy(say_start={"en": "One moment", "ar": "لحظة"}, say_done={"en": "Done"})) == []
    assert any("say_start" in e for e in validate(http_policy(say_start="Just a moment")))
    assert any("say_start" in e for e in validate(http_policy(say_start={"fr": "Un instant"})))


def test_catalog_builds_the_new_fields():
    cfg = {"skills": {"line": {"notify": http_policy(enabled=False, **{"async": True},
                                                      say_start={"en": "Notifying"}, say_done={"en": "Notified"}),
                               "plain": http_policy()}}}
    cat = Catalog.from_config(cfg, {}, {})
    t, p = cat.get("notify"), cat.get("plain")
    assert (t.enabled, t.run_async, t.say_start, t.say_done) == (False, True, {"en": "Notifying"}, {"en": "Notified"})
    assert (p.enabled, p.run_async, p.say_start, p.say_done) == (True, False, None, None)


# ---------------------------------------------------------------- active / inactive


async def test_inactive_tool_is_hidden_from_the_model_and_refuses_to_run(monkeypatch):
    backend = Backend(monkeypatch)
    agent, *_ = await make_agent("start: a\nnodes:\n  - {id: a, type: conversation, say: {en: Hi}}")
    ex = agent.executor
    ex.catalog.tools["notify"] = http_def(enabled=False)
    ex.catalog.tools["notify2"] = http_def("notify2")
    names = {t["function"]["name"] for t in ex.llm_tools()}
    assert "notify" not in names and "notify2" in names
    assert "notify" not in {t["function"]["name"] for t in ex.llm_tools({"notify", "notify2"})}
    r = await ex.execute(ToolCall(id="1", name="notify", arguments={"order": "A1"}), ToolContext(call_id="c"), EventBus().bind())
    assert not r.ok and "inactive" in r.error and backend.calls == []


# ---------------------------------------------------------------- async


async def test_async_tool_answers_at_once_and_finishes_in_the_background(monkeypatch):
    backend = Backend(monkeypatch, delay=0.2)
    agent, *_ = await make_agent("start: a\nnodes:\n  - {id: a, type: conversation, say: {en: Hi}}")
    agent.executor.catalog.tools["notify"] = http_def(run_async=True)
    t0 = asyncio.get_running_loop().time()
    r = await run(agent)
    assert r.ok and r.data == {"success": True, "queued": True}
    assert asyncio.get_running_loop().time() - t0 < 0.15          # did not wait for the API
    await asyncio.wait_for(asyncio.gather(*agent.executor._background), 2)
    assert backend.calls == [("https://api.example.com/notify", None)]


async def test_async_tool_failure_does_not_reach_the_agent(monkeypatch):
    def boom(request):
        raise httpx.ConnectError("down")
    monkeypatch.setattr(http_tool, "_client", httpx.AsyncClient(transport=httpx.MockTransport(boom)))
    monkeypatch.setattr(http_tool, "simulated", lambda kind: None)
    agent, *_ = await make_agent("start: a\nnodes:\n  - {id: a, type: conversation, say: {en: Hi}}")
    agent.executor.catalog.tools["notify"] = http_def(run_async=True)
    r = await run(agent)
    assert r.ok and r.data["queued"]
    await asyncio.wait_for(asyncio.gather(*agent.executor._background), 2)       # the background error is only logged


async def test_async_does_not_apply_to_reads(monkeypatch):
    Backend(monkeypatch)
    agent, *_ = await make_agent("start: a\nnodes:\n  - {id: a, type: conversation, say: {en: Hi}}")
    agent.executor.catalog.tools["lookup"] = http_def("lookup", kind="read", run_async=True,
                                                      http={"method": "GET", "url": "https://api.example.com/x"})
    r = await run(agent, "lookup")
    assert r.ok and r.data == {"success": True}


# ---------------------------------------------------------------- start / done messages


async def test_start_and_done_lines_are_spoken_in_the_callers_language(monkeypatch):
    Backend(monkeypatch)
    agent, s, llm, out = await make_agent("start: a\nnodes:\n  - {id: a, type: conversation, say: {en: Hi}}")
    agent.executor.catalog.tools["notify"] = http_def(
        say_start={"en": "Notifying the warehouse about {{args.order}}.", "ar": "أبلغ المستودع"},
        say_done={"en": "The warehouse knows.", "ar": "تم إبلاغ المستودع"})
    r = await run(agent)
    assert r.ok and out.said == ["Notifying the warehouse about A1.", "The warehouse knows."]


async def test_arabic_caller_gets_the_arabic_lines_and_a_failure_gets_no_done_line(monkeypatch):
    Backend(monkeypatch)
    agent, s, llm, out = await make_agent("start: a\nnodes:\n  - {id: a, type: conversation, say: {en: Hi, ar: أهلا}}", language="ar")
    agent.executor.catalog.tools["notify"] = http_def(say_start={"en": "Starting", "ar": "أبدأ"}, say_done={"en": "Done", "ar": "تم"})
    await run(agent)
    assert out.said == ["أبدأ", "تم"]
    out.said.clear()
    ctx = agent._ctx()
    ctx.allowed_tools = None
    await agent._execute_with_filler(ToolCall(id="2", name="notify", arguments={}), ctx, EventBus().bind())   # invalid
    assert out.said == ["أبدأ"]


async def test_start_line_replaces_the_generic_filler(monkeypatch):
    Backend(monkeypatch, delay=0.3)
    agent, s, llm, out = await make_agent("start: a\nnodes:\n  - {id: a, type: conversation, say: {en: Hi}}")
    agent.filler_after_s = 0.05
    agent.executor.catalog.tools["notify"] = http_def(say_start={"en": "Notifying."})
    await run(agent)
    assert out.said == ["Notifying."]            # no extra "one moment" filler


@pytest.mark.parametrize("field", ["say_start", "say_done"])
async def test_tools_without_lines_say_nothing(monkeypatch, field):
    Backend(monkeypatch)
    agent, s, llm, out = await make_agent("start: a\nnodes:\n  - {id: a, type: conversation, say: {en: Hi}}")
    agent.executor.catalog.tools["notify"] = http_def()
    await run(agent)
    assert out.said == []
