"""Hamsa parity (Call History): live instructions to a running call, the duration filter."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.control import api as control_api
from runtime.harness.context import system_prompt
from runtime.server import app as server_app

from .test_flow_options import make_agent

FLOW = "start: a\nnodes:\n  - {id: a, type: conversation, say: {en: Hi}}"


async def test_a_supervisor_instruction_reaches_the_next_prompt():
    agent, s, llm, out = await make_agent(FLOW)
    assert "supervisor" not in system_prompt(s, agent.skills).lower()
    s.supervisor_notes += ["Offer the 5 pm slot first.", "Keep answers to one sentence."]
    prompt = system_prompt(s, agent.skills)
    assert "Live instructions from a supervisor" in prompt and "- Offer the 5 pm slot first." in prompt
    assert prompt.index("Live instructions") > prompt.index("Call facts")          # after the facts, with the task
    s.supervisor_notes += [f"note {i}" for i in range(10)]
    assert "Offer the 5 pm slot first." not in system_prompt(s, agent.skills)      # only the latest five count
    assert "note 9" in system_prompt(s, agent.skills)


def test_the_voice_call_keeps_and_logs_the_note():
    from types import SimpleNamespace

    from runtime.events import EventType
    from runtime.harness.session import Session
    from runtime.voice.call import VoiceCall
    emitted = []
    call = SimpleNamespace(session=Session(call_id="c"), _stopped=False,
                           ev=SimpleNamespace(emit=lambda t, **kw: emitted.append((t, kw))))
    VoiceCall.add_supervisor_note(call, "  Say   sorry   first.\n")
    assert call.session.supervisor_notes == ["Say sorry first."]
    assert emitted == [(EventType.SLOT_SET, {"field": "supervisor_instruction", "value": "Say sorry first."})]
    call._stopped = True
    VoiceCall.add_supervisor_note(call, "too late")                                  # the call is over
    VoiceCall.add_supervisor_note(call, "   ")
    assert call.session.supervisor_notes == ["Say sorry first."]


@pytest.fixture
def client(monkeypatch):
    sent = []

    class Rt:
        live = None
        settings = None

        async def instruct_call(self, call_id, text):
            sent.append((call_id, text))
            return call_id == "live-1"
    monkeypatch.setitem(server_app.state, "rt", Rt())
    app = FastAPI()
    app.include_router(control_api.router)
    app.dependency_overrides = {d.dependency: (lambda: None) for d in control_api.auth}
    with TestClient(app) as c:
        yield c, sent


def test_instruction_endpoint(client, monkeypatch):
    c, sent = client

    async def scope(*a, **k):
        return None
    monkeypatch.setattr(control_api, "project_scope", scope)
    r = c.post("/api/calls/live-1/instruction", json={"text": "  Offer   a callback "})
    assert r.status_code == 200 and sent == [("live-1", "Offer a callback")]
    assert c.post("/api/calls/gone/instruction", json={"text": "hello"}).status_code == 404         # not running
    assert c.post("/api/calls/live-1/instruction", json={"text": "   "}).status_code == 422
    assert c.post("/api/calls/live-1/instruction", json={"text": "x" * 501}).status_code == 422


def test_duration_filter_arguments():
    from fastapi import HTTPException
    d = control_api._duration
    assert d(None, None, None) is None and d("gt", 30, None) == ("gt", 30, None) and d("between", 10, 60) == ("between", 10, 60)
    for bad in (("sideways", 1, None), ("gt", None, None), ("gt", -1, None), ("between", 60, 10), ("between", 5, None)):
        with pytest.raises(HTTPException) as e:
            d(*bad)
        assert e.value.status_code == 422
