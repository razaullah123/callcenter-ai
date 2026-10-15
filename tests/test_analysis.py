"""Post-call analysis (Hamsa parity: Outcome + Satisfaction): settings, prompt, validation, the sink, webhook, API."""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from runtime.events import Event, EventType
from runtime.platform import analysis
from runtime.platform.bundle import validate as validate_bundle
from runtime.platform.store import MemoryStore
from runtime.providers import TextDelta

CFG = {"enabled": True, "fields": [
    {"name": "reason", "type": "enum", "options": ["booking", "billing", "other"], "description": "why they called"},
    {"name": "wants_callback", "type": "boolean", "description": "asked to be called back"},
    {"name": "branch", "type": "string", "description": "the branch named"},
    {"name": "visits", "type": "number", "description": "how many visits"}]}
LINES = [{"role": "agent", "text": "Hello"}, {"role": "user", "text": "I want to book"}, {"role": "agent", "text": "Which day?"},
         {"role": "user", "text": "Tomorrow, thanks"}, {"role": "system", "text": "handoff: x"}]
GOOD = {"summary": "The caller booked an appointment.", "sentiment": "Positive", "csat": 5, "nps": 9, "resolved": True,
        "fields": {"reason": "Booking", "wants_callback": "no", "branch": " Olaya ", "visits": "3"}}


class LLM:
    """A model that answers with `reply` (or raises / hangs)."""
    model = "fake-model"

    def __init__(self, reply="", error=None, hang=False):
        self.reply, self.error, self.hang, self.seen = reply, error, hang, []

    async def stream(self, messages, **kw):
        self.seen.append(messages)
        if self.hang:
            await asyncio.sleep(10)
        if self.error:
            raise self.error
        for i in range(0, len(self.reply), 7):
            yield TextDelta(self.reply[i:i + 7])


# ---------------------------------------------------------------- settings

def test_settings_are_validated():
    assert analysis.analysis_errors(None) == [] and analysis.analysis_errors(CFG) == []
    assert analysis.analysis_errors({"enabled": "yes"}) and analysis.analysis_errors("x")
    bad = lambda f: " | ".join(analysis.analysis_errors({"fields": f}))      # noqa: E731
    assert "snake_case" in bad([{"name": "Reason", "type": "string"}])
    assert "twice" in bad([{"name": "a", "type": "string"}, {"name": "a", "type": "string"}])
    assert "type must be" in bad([{"name": "a", "type": "date"}])
    assert "options" in bad([{"name": "a", "type": "enum", "options": ["only"]}])
    assert "300" in bad([{"name": "a", "type": "string", "description": "x" * 301}])
    assert "at most 20" in bad([{"name": f"f{i}", "type": "string"} for i in range(21)])
    assert validate_bundle({"schema": 2, "analysis": {"fields": [{"name": "Bad", "type": "string"}]}})       # the publish check sees it


# ---------------------------------------------------------------- prompt + validation

def test_prompt_follows_the_settings():
    p = analysis.build_prompt(CFG, "ar")
    assert "Arabic" in p and '"summary"' in p and '"csat"' in p and '"resolved"' in p
    assert '"reason" (one of booking | billing | other): why they called' in p and '"visits" (number)' in p
    q = analysis.build_prompt({"summary": False, "sentiment": True, "satisfaction": False}, None)
    assert '"summary"' not in q and '"csat"' not in q and '"sentiment"' in q and "fields" not in q


def test_the_models_answer_is_checked_not_trusted():
    v = analysis.validate(GOOD, CFG)
    assert v["summary"] == "The caller booked an appointment." and v["sentiment"] == "positive" and (v["csat"], v["nps"]) == (5, 9)
    assert v["outcome"] == {"reason": "booking", "wants_callback": False, "branch": "Olaya", "visits": 3.0}
    bad = analysis.validate({"sentiment": "angry", "csat": 9, "nps": -1, "resolved": "yes", "summary": 5,
                             "fields": {"reason": "weather", "wants_callback": "maybe", "visits": "many", "branch": ""}}, CFG)
    assert (bad["sentiment"], bad["csat"], bad["nps"], bad["resolved"], bad["summary"]) == (None, None, None, None, None)
    assert bad["outcome"] == {"reason": None, "wants_callback": None, "branch": None, "visits": None}
    assert analysis.validate({"csat": 4.6, "nps": "8"}, {})["csat"] == 5 and analysis.validate({"csat": True}, {})["csat"] is None
    off = analysis.validate(GOOD, {"summary": False, "sentiment": False, "satisfaction": False})
    assert (off["summary"], off["sentiment"], off["csat"], off["resolved"], off["outcome"]) == (None, None, None, None, {})


def test_json_is_found_in_prose_and_fences():
    assert analysis.parse_json('Sure!\n```json\n{"a": {"b": 1}, "c": "}"}\n```') == {"a": {"b": 1}, "c": "}"}
    assert analysis.parse_json("no json") == {} and analysis.parse_json("{broken") == {} and analysis.parse_json('[1] {"x": 1}') == {"x": 1}


def test_transcript_text_is_bounded_and_skips_system_lines():
    t = analysis.transcript_text(LINES)
    assert t == "Agent: Hello\nUser: I want to book\nAgent: Which day?\nUser: Tomorrow, thanks"
    long = analysis.transcript_text([{"role": "user", "text": "x" * 20000}])
    assert len(long) < analysis.MAX_TRANSCRIPT_CHARS + 10 and "…" in long


async def test_analyze_handles_every_way_a_model_can_fail(monkeypatch):
    llm = LLM(json.dumps(GOOD))
    values, error = await analysis.analyze(llm, CFG, LINES, "en")
    assert error is None and values["sentiment"] == "positive" and "Transcript:" in llm.seen[0][1]["content"]
    assert (await analysis.analyze(LLM("I cannot do that"), CFG, LINES, "en"))[1] == "the analysis model did not return JSON"
    assert "failed (RuntimeError)" in (await analysis.analyze(LLM(error=RuntimeError("boom")), CFG, LINES, "en"))[1]
    monkeypatch.setattr(analysis, "TIMEOUT_S", 0.05)
    assert "in time" in (await analysis.analyze(LLM(hang=True), CFG, LINES, "en"))[1]


# ---------------------------------------------------------------- the numbers

def test_satisfaction_numbers():
    rows = [{"status": "ok", "csat": 5, "nps": 10, "sentiment": "positive", "resolved": True},
            {"status": "ok", "csat": 4, "nps": 9, "sentiment": "positive", "resolved": True},
            {"status": "ok", "csat": 2, "nps": 3, "sentiment": "negative", "resolved": False},
            {"status": "ok", "csat": None, "nps": 8, "sentiment": "neutral", "resolved": None},
            {"status": "skipped"}, {"status": "failed"}]
    s = analysis.satisfaction_summary(rows)
    assert (s["analyzed"], s["skipped"], s["failed"]) == (4, 1, 1)
    assert s["csat"] == {"score": 66.7, "average": 3.67, "calls": 3}                      # 2 of 3 rated 4-5
    assert s["nps"] == {"score": 25, "calls": 4}                                          # 2 promoters - 1 detractor of 4
    assert s["sentiment"] == {"positive": 50.0, "neutral": 25.0, "negative": 25.0, "calls": 4}
    assert s["resolved"] == {"score": 66.7, "calls": 3}
    empty = analysis.satisfaction_summary([])
    assert empty["csat"]["score"] is None and empty["nps"]["score"] is None and empty["sentiment"]["positive"] is None


# ---------------------------------------------------------------- the sink

class World:
    def __init__(self, cfg, llm, lines=LINES, monkeypatch=None):
        self.store = MemoryStore()
        self.llm = llm
        loaded = SimpleNamespace(llm=llm)

        async def load_release(rid):
            return loaded
        self.rt = SimpleNamespace(platform=self.store, store=None, loader=SimpleNamespace(load_release=load_release))
        self.sink = analysis.AnalysisSink(self.rt)
        self.cfg, self.lines = cfg, lines
        started = datetime.now(timezone.utc)

        async def get_call(call_id, scope=None):
            return {"call": {"agent_id": "a1", "release_id": self.rid, "language": "en", "started_at": started, "ended_at": started},
                    "transcript": self.lines}
        monkeypatch.setattr("runtime.control.store.get_call", get_call)

    async def start(self, call_id="c1"):
        await self.store.put_agent({"id": "a1", "name": "A", "workspace_id": "hmg"})
        r = await self.store.add_release("a1", {"schema": 2, **({"analysis": self.cfg} if self.cfg is not None else {})}, "t", "n")
        self.rid = r["id"] if isinstance(r, dict) else r
        await self.sink(Event(type=EventType.CALL_START, call_id=call_id, data={"agent_id": "a1", "release_id": self.rid}))

    async def end(self, call_id="c1"):
        await self.sink(Event(type=EventType.CALL_END, call_id=call_id, data={}))


async def test_a_call_that_ends_is_analysed_and_stored(monkeypatch):
    w = World(CFG, LLM(json.dumps(GOOD)), monkeypatch=monkeypatch)
    await w.start()
    await w.end()
    row = await w.sink.result("c1", 5)
    assert row["status"] == "ok" and row["sentiment"] == "positive" and row["model"] == "fake-model"
    saved = await w.store.call_analysis("c1")
    assert saved["workspace_id"] == "hmg" and saved["agent_id"] == "a1" and saved["outcome"]["reason"] == "booking"
    pub = analysis.public(saved)
    assert pub["csat"] == 5 and "workspace_id" not in pub and pub["summary"].startswith("The caller")
    await w.end()                                                                       # a second CALL_END doesn't run it twice
    assert len(w.llm.seen) == 1


async def test_nothing_runs_when_it_is_off_and_short_calls_are_skipped(monkeypatch):
    off = World({"enabled": False}, LLM("{}"), monkeypatch=monkeypatch)
    await off.start()
    await off.end()
    assert await off.sink.result("c1", 5) is None and await off.store.call_analysis("c1") is None and off.llm.seen == []
    none = World(None, LLM("{}"), monkeypatch=monkeypatch)                               # a release without the section
    await none.start()
    await none.end()
    assert await none.sink.result("c1", 5) is None
    short = World(CFG, LLM(json.dumps(GOOD)), lines=LINES[:2], monkeypatch=monkeypatch)
    await short.start()
    await short.end()
    row = await short.sink.result("c1", 5)
    assert row["status"] == "skipped" and "fewer than 2" in row["error"] and short.llm.seen == []
    assert (await short.store.call_analysis("c1"))["status"] == "skipped"


async def test_a_failing_model_is_recorded_and_never_raises(monkeypatch):
    w = World(CFG, LLM(error=RuntimeError("down")), monkeypatch=monkeypatch)
    await w.start()
    await w.end()
    row = await w.sink.result("c1", 5)
    assert row["status"] == "failed" and "RuntimeError" in row["error"] and row["summary"] is None
    assert await w.sink.result("unknown-call", 0.1) is None


async def test_results_are_not_kept_forever(monkeypatch):
    monkeypatch.setattr(analysis, "KEEP_RESULT_S", 0.05)
    w = World(CFG, LLM(json.dumps(GOOD)), monkeypatch=monkeypatch)
    await w.start()
    await w.end()
    assert await w.sink.result("c1", 5)
    await asyncio.sleep(0.15)
    assert "c1" not in w.sink._calls


# ---------------------------------------------------------------- the webhook carries it

async def test_call_ended_webhook_includes_the_analysis(monkeypatch):
    from .test_webhook import Hook
    h = Hook({"url": "https://hook.example/x"})
    row = {"status": "ok", "summary": "Booked.", "sentiment": "positive", "csat": 5, "nps": 9, "resolved": True,
           "outcome": {"reason": "booking"}}

    class Sink:
        asked = []

        async def result(self, call_id, timeout):
            self.asked.append(call_id)
            return row
    h.rt.analysis = Sink()
    h.calls["c1"] = SimpleNamespace(session=SimpleNamespace(params={"customer_name": "Sara"}))
    await h.start()

    async def get_call(call_id, scope=None):
        return {"call": {"agent_id": "a1"}, "transcript": [{"role": "user", "text": "hi"}]}
    monkeypatch.setattr("runtime.control.store.get_call", get_call)
    await h.sink(Event(type=EventType.CALL_END, call_id="c1", data={}))
    await h.settle()
    data = h.posts[-1]["body"]["data"]["data"]
    assert data["outcomeResult"] == {"customer_name": "Sara", "reason": "booking"}
    assert data["analysis"] == {"summary": "Booked.", "sentiment": "positive", "csat": 5, "nps": 9, "resolved": True}
    assert Sink.asked == ["c1"]
    from runtime.platform.webhook import ended_data
    assert "analysis" not in ended_data("c", {}, [], True, {}, {"status": "failed", "error": "x"})
    assert "analysis" not in ended_data("c", {}, [], True, {}, None)


# ---------------------------------------------------------------- the API

@pytest.fixture
def api(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from runtime.config import get_settings
    from runtime.control import api as control_api
    from runtime.platform.seed import seed
    from runtime.server import app as server_app

    from .test_studio import _Rt
    loop = asyncio.new_event_loop()
    store = MemoryStore()
    loop.run_until_complete(seed(store, get_settings()))
    rt = _Rt(store, None, SimpleNamespace(load_release=None), None)

    async def flush():
        return None
    rt.store = SimpleNamespace(flush=flush)
    monkeypatch.setitem(server_app.state, "rt", rt)
    app = FastAPI()
    app.include_router(control_api.router)
    app.dependency_overrides = {d.dependency: (lambda: None) for d in control_api.auth}

    async def scope(*a, **k):
        return None
    monkeypatch.setattr(control_api, "project_scope", scope)
    with TestClient(app) as c:
        yield c, rt, store, loop, control_api


def test_satisfaction_endpoint(api):
    c, rt, store, loop, _ = api
    now = datetime.now(timezone.utc)
    for i, (csat, nps, senti) in enumerate([(5, 10, "positive"), (2, 4, "negative"), (4, 9, "positive")]):
        loop.run_until_complete(store.put_call_analysis({
            "call_id": f"c{i}", "workspace_id": "hmg", "agent_id": "hmg-care" if i < 2 else "other", "started_at": now - timedelta(hours=1),
            "status": "ok", "csat": csat, "nps": nps, "sentiment": senti, "resolved": csat > 3}))
    loop.run_until_complete(store.put_call_analysis({"call_id": "old", "workspace_id": "hmg", "agent_id": "hmg-care",
                                                      "started_at": now - timedelta(days=3), "status": "ok", "csat": 1, "nps": 0}))
    loop.run_until_complete(store.put_call_analysis({"call_id": "x", "workspace_id": "clinic", "agent_id": "z", "started_at": now,
                                                      "status": "ok", "csat": 1, "nps": 0}))
    s = c.get("/api/analytics/satisfaction").json()                                       # the last 24 hours, this project
    assert s["analyzed"] == 3 and s["csat"]["score"] == 66.7 and s["nps"]["score"] == 33
    one = c.get("/api/analytics/satisfaction", params={"agent": "hmg-care"}).json()
    assert one["analyzed"] == 2 and one["sentiment"]["negative"] == 50.0
    wide = c.get("/api/analytics/satisfaction", params={"start": (now - timedelta(days=7)).isoformat(), "end": now.isoformat()}).json()
    assert wide["analyzed"] == 4


def test_analyze_now_runs_on_an_ended_call(api, monkeypatch):
    c, rt, store, loop, control_api = api
    ended = datetime.now(timezone.utc)
    calls = {"c1": {"call": {"agent_id": "hmg-care", "release_id": None, "language": "en", "started_at": ended, "ended_at": ended}, "transcript": LINES},
             "live": {"call": {"agent_id": "hmg-care", "ended_at": None}, "transcript": []}}

    async def get_call(call_id, scope=None):
        return calls.get(call_id)
    monkeypatch.setattr("runtime.control.store.get_call", get_call)
    monkeypatch.setattr(control_api.store, "get_call", get_call)
    llm = LLM(json.dumps(GOOD))
    rt.loader = SimpleNamespace(load_release=None, for_call=lambda **kw: asyncio.sleep(0, SimpleNamespace(llm=llm)))
    r = c.post("/api/calls/c1/analyze")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok" and body["csat"] == 5 and body["call_id"] == "c1"
    assert body["outcome"] == {}                                   # this release has no outcome fields of its own
    assert loop.run_until_complete(store.call_analysis("c1"))["sentiment"] == "positive"
    assert c.post("/api/calls/live/analyze").status_code == 409 and c.post("/api/calls/nope/analyze").status_code == 404


# ---------------------------------------------------------------- Hamsa comparison: field types, imported outcome schema, "why no analysis"

def test_array_and_object_fields():
    f = {"name": "services", "type": "array"}
    g = {"name": "details", "type": "object"}
    assert analysis.analysis_errors({"fields": [f, g]}) == []
    cfg = {"fields": [f, g]}
    assert "a JSON list" in analysis.build_prompt(cfg, "en") and "a JSON object" in analysis.build_prompt(cfg, "en")
    v = analysis.validate({"fields": {"services": ["dental", 2, None, ["x"], {"y": 1}], "details": {"city": "Riyadh", "n": 3, "bad": [1], "none": None}}}, cfg)
    assert v["outcome"] == {"services": ["dental", 2], "details": {"city": "Riyadh", "n": 3}}
    v = analysis.validate({"fields": {"services": "dental", "details": ["x"]}}, cfg)
    assert v["outcome"] == {"services": None, "details": None}
    assert analysis.validate({"fields": {"services": [], "details": {}}}, cfg)["outcome"] == {"services": None, "details": None}


def test_a_hamsa_outcome_schema_becomes_our_fields():
    shape = {"type": "object", "properties": {
        "clinic": {"type": "string", "description": "The clinic involved; empty if none."},
        "callOutcome": {"type": "string", "enum": ["booked", "no_action", "transferred"], "description": "How it ended"},
        "Caller Sentiment": {"type": "string", "enum": ["positive", "neutral", "negative"]},
        "objective_met": {"type": "boolean", "description": "x" * 400},
        "visits": {"type": "integer"}, "tags": {"type": "array"}, "extra": {"type": "object"},
        "clinic ": {"type": "string"}, "1bad": {"type": "string"}, "": {"type": "string"}, "weird": "not a spec",
        "mixed": {"type": "number", "enum": ["a", "b"]}}}
    fields = analysis.fields_from_schema(shape)
    by = {f["name"]: f for f in fields}
    assert list(by) == ["clinic", "call_outcome", "caller_sentiment", "objective_met", "visits", "tags", "extra", "mixed"]
    assert by["call_outcome"] == {"name": "call_outcome", "type": "enum", "description": "How it ended", "options": ["booked", "no_action", "transferred"]}
    assert (by["visits"]["type"], by["tags"]["type"], by["extra"]["type"], by["mixed"]["type"]) == ("number", "array", "object", "number")
    assert len(by["objective_met"]["description"]) == 300 and analysis.analysis_errors({"fields": fields}) == []
    assert analysis.fields_from_schema(None) == [] and analysis.fields_from_schema({"properties": []}) == []
    many = {"type": "object", "properties": {f"f{i}": {"type": "string"} for i in range(30)}}
    assert len(analysis.fields_from_schema(many)) == analysis.MAX_FIELDS


def test_imported_settings_keep_the_built_ins_that_the_schema_lacks():
    s = analysis.settings_from_schema({"properties": {"summary": {"type": "string"}, "caller_sentiment": {"type": "string"}, "x": {"type": "string"}}})
    assert s["enabled"] and s["summary"] is False and s["sentiment"] is False and s["satisfaction"] is True and len(s["fields"]) == 3
    t = analysis.settings_from_schema({"properties": {"doctor": {"type": "string"}}})
    assert t["summary"] is True and t["sentiment"] is True
    assert analysis.settings_from_schema({"properties": {}}) is None and analysis.settings_from_schema(None) is None


def test_importing_a_hamsa_agent_brings_its_outcome_schema():
    import copy

    from runtime.platform import hamsa_import

    from . import test_projects
    h = copy.deepcopy(test_projects.HAMSA)
    h["outcomeResponseShape"] = {"type": "object", "properties": {
        "summary": {"type": "string", "description": "One clear paragraph"}, "doctor": {"type": "string", "description": "The doctor"}}}
    parts = hamsa_import.convert(h)
    assert parts["analysis"]["enabled"] and [f["name"] for f in parts["analysis"]["fields"]] == ["summary", "doctor"]
    assert parts["analysis"]["summary"] is False and parts["source"]["outcome_schema"]
    assert hamsa_import.convert(copy.deepcopy(test_projects.HAMSA))["analysis"] is None          # no schema, nothing switched on


async def test_the_card_can_say_why_there_is_no_analysis(monkeypatch):
    from runtime.control import api as control_api
    store = MemoryStore()
    await store.put_agent({"id": "a1", "name": "A", "workspace_id": "hmg"})
    r1 = await store.add_release("a1", {"schema": 2}, "t", "n")                                 # a version without analysis
    r2 = await store.add_release("a1", {"schema": 2, "analysis": {"enabled": True}}, "t", "n")  # a version with it
    await store.set_draft("a1", {"schema": 2, "analysis": {"enabled": True}}, "t")
    monkeypatch.setattr(control_api, "_rt", lambda: SimpleNamespace(platform=store))
    rid = lambda r: r["id"] if isinstance(r, dict) else r                                       # noqa: E731
    old = await control_api._analysis_setup({"agent_id": "a1", "release_id": rid(r1)})
    assert old == {"agent_id": "a1", "release_version": 1, "in_release": False, "in_draft": True}      # on in the draft, not published
    new = await control_api._analysis_setup({"agent_id": "a1", "release_id": rid(r2)})
    assert new["in_release"] is True and new["release_version"] == 2
    assert await control_api._analysis_setup({"agent_id": None}) is None


def test_analyze_now_uses_the_agents_current_fields(api, monkeypatch):
    c, rt, store, loop, control_api = api
    now = datetime.now(timezone.utc)

    async def get_call(call_id, scope=None):
        return {"call": {"agent_id": "hmg-care", "release_id": None, "language": "en", "started_at": now, "ended_at": now}, "transcript": LINES}
    monkeypatch.setattr("runtime.control.store.get_call", get_call)
    monkeypatch.setattr(control_api.store, "get_call", get_call)
    loop.run_until_complete(store.set_draft("hmg-care", {"schema": 2, "analysis": {"enabled": True, "fields": CFG["fields"]}}, "t"))
    llm = LLM(json.dumps(GOOD))
    rt.loader = SimpleNamespace(load_release=None, for_call=lambda **kw: asyncio.sleep(0, SimpleNamespace(llm=llm)))
    body = c.post("/api/calls/c9/analyze").json()
    assert body["outcome"] == {"reason": "booking", "wants_callback": False, "branch": "Olaya", "visits": 3.0}      # the draft's fields
