"""Hamsa parity: the Variable System — system variables, custom variables with call params, naming rules, template
fallbacks, JSONPath, typed extraction."""

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.events import EventBus
from runtime.harness.handoff import AgentTransfer, carry_over
from runtime.harness.nlu.extract import check_value
from runtime.harness.session import Session
from runtime.harness.variables import (RESERVED, SYSTEM_VARIABLES, build_custom, declared_variables, name_problem,
                                       variable_errors)
from runtime.platform.bundle import validate
from runtime.skills import Graph
from runtime.skills.flow import _eval, jsonpath_to_path, render
from runtime.skills.loader import template_vars

from .test_flow_agents import TwoAgentRt, make_loaded, read_until
from .test_projects import console  # noqa: F401  (fixture)


# ---------------------------------------------------------------- naming rules


@pytest.mark.parametrize("name", ["account_number", "a", "x1_y2", "a" * 50, "customer_name"])
def test_good_names(name):
    assert name_problem(name) is None


@pytest.mark.parametrize("name", ["Customer_Name", "123_customer", "_customer", "customer-name", "customer name", "customerName",
                                  "customer_name!", "", "a" * 51])
def test_bad_names(name):
    assert "snake_case" in name_problem(name)


def test_system_names_are_reserved():
    assert "is a system variable" in name_problem("call_id") and "is a system variable" in name_problem("current_time")
    assert RESERVED >= {n for _, n, _ in SYSTEM_VARIABLES} and "userNumber" in RESERVED


def test_graph_checks_variable_names():
    g = Graph.from_dict({"nodes": [
        {"id": "a", "dtmf_capture": {"variable": "pin"}},
        {"id": "b", "type": "set", "set": {"Total": "=1", "call_id": "=x", "fine_name": "=1"}},
        {"id": "c", "type": "tool", "tool": "t", "outputs": {"orderStatus": "result.x"}}],
        "variables": {"tooLong" + "x" * 50: {}, "ok_var": {}}})
    errors = " | ".join(g.errors())
    assert "'Total'" in errors and "'call_id'" in errors and "is a system variable" in errors and "'orderStatus'" in errors
    assert "'fine_name'" not in errors and "'ok_var'" not in errors and "'pin'" not in errors


# ---------------------------------------------------------------- custom variables


DECLARED = {"business_name": {"type": "string", "default": "Cloud Clinic"}, "visits": {"type": "number", "default": 0},
            "vip": {"type": "boolean", "default": False}, "tags": {"type": "array", "default": []},
            "profile": {"type": "object", "default": {"tier": "basic"}}, "note": {"type": "string"}}


def test_custom_variable_declarations_are_validated():
    assert variable_errors(DECLARED) == []
    assert validate({"schema": 2, "models": {k: {"type": "x"} for k in ("llm", "stt", "tts")}, "skills": {"a": 1},
                     "variables": DECLARED}) == []
    bad = variable_errors({"Bad Name": {}, "call_id": {}, "n": {"type": "float"}, "m": {"type": "number", "default": "x"}})
    text = " | ".join(bad)
    assert "Bad Name" in text and "call_id" in text and "type must be one of" in text and "doesn't fit the type number" in text
    assert validate({"schema": 2, "models": {k: {"type": "x"} for k in ("llm", "stt", "tts")}, "skills": {"a": 1},
                     "variables": {"X": {}}})


def test_params_override_defaults_and_follow_the_declared_types():
    assert build_custom(DECLARED, None) == {"business_name": "Cloud Clinic", "visits": 0, "vip": False, "tags": [],
                                            "profile": {"tier": "basic"}, "note": ""}
    got = build_custom(DECLARED, {"business_name": "Sara's", "visits": "3", "vip": "yes", "tags": "[\"a\", \"b\"]",
                                  "profile": "{\"tier\": \"gold\"}", "extra_value": 7})
    assert got["business_name"] == "Sara's" and got["visits"] == 3 and got["vip"] is True
    assert got["tags"] == ["a", "b"] and got["profile"] == {"tier": "gold"} and got["extra_value"] == 7      # undeclared: kept
    assert build_custom(DECLARED, {"visits": "2.5"})["visits"] == 2.5


@pytest.mark.parametrize("params, message", [
    ({"Bad": 1}, "snake_case"), ({"call_id": "x"}, "system variable"), ({"visits": "many"}, "expected number"),
    ({"vip": "maybe"}, "expected boolean"), ({"tags": {"a": 1}}, "expected array"), ("nope", "must be an object"),
    ({"big": "x" * 60_000}, "too large")])
def test_bad_params_are_refused(params, message):
    with pytest.raises(ValueError, match=message):
        build_custom(DECLARED, params)


def test_custom_variables_reach_templates_but_never_replace_system_ones():
    s = Session(call_id="chat-1", ani="+966548802968", agent_name="Line", agent_id="line-agent")
    s.custom = build_custom(DECLARED, {"business_name": "Acme", "call_id": "x"} if False else {"business_name": "Acme"})
    v = template_vars(s)
    assert v["business_name"] == "Acme" and v["visits"] == 0 and v["agent_id"] == "line-agent"
    s.custom["call_id"] = "hijacked"
    assert template_vars(s)["call_id"] == "chat-1"                          # a system value always wins
    assert render("Welcome to {{ business_name }} ({{ call_type }})", {"slots": {}, **template_vars(s)}) == "Welcome to Acme (chat)"


def test_system_variables_cover_hamsas_list():
    s = Session(call_id="ivr-9", ani="0548802968")
    v = template_vars(s)
    for _, name, _ in SYSTEM_VARIABLES:
        assert name in v, name
    assert v["call_type"] == "phone" and Session(call_id="call-1").agent_id == "" and template_vars(Session(call_id="call-1"))["call_type"] == "web"
    assert len(v["current_time"]) == 5 and v["current_year"].isdigit() and 1 <= int(v["current_month"]) <= 12
    assert v["current_timestamp"].isdigit() and len(v["current_timestamp"]) >= 13 and v["call_start_time"].startswith("20")


def test_system_variable_endpoint(console):  # noqa: F811
    c, rt, store, loop = console
    rows = c.get("/api/variables/system").json()
    assert {r["name"] for r in rows} == {n for _, n, _ in SYSTEM_VARIABLES} and rows[0]["group"] == "Time"
    assert all(r["description"] for r in rows)


def test_carry_over_reads_the_same_params_against_the_next_agent():
    old = Session(call_id="c-1", params={"business_name": "Acme", "visits": "4"})
    new = carry_over(old, "B", AgentTransfer("b"), agent_id="b", declared={"visits": {"type": "number", "default": 0}})
    assert new.agent_id == "b" and new.params == old.params and new.custom == {"visits": 4, "business_name": "Acme"}
    other = carry_over(old, "C", AgentTransfer("c"), agent_id="c", declared={"visits": {"type": "boolean"}})      # doesn't fit: defaults
    assert other.custom == {"visits": ""}


# ---------------------------------------------------------------- templates and paths


def test_fallback_syntax_in_templates():
    ctx = {"slots": {}, "balance": "120", "empty": "", "user": {"name": "Sara"}}
    assert render("{{balance || 'unavailable'}}", ctx) == "120"
    assert render("{{ missing || 'unavailable' }}", ctx) == "unavailable"
    assert render("{{ empty || 'n/a' }}", ctx) == "n/a"                                   # empty counts as missing
    assert render("{{ user.nick || user.name }}", ctx) == "Sara"
    assert render("Hi {{ name | default('there') }}", ctx) == "Hi there"                  # plain Jinja still works
    assert render("{% if balance %}has{% endif %} {{ x || 'a' }}-{{ y || 'b' }}", ctx) == "has a-b"


def test_jsonpath_for_tool_results():
    result = {"data": {"user": {"email": "a@b.c"}, "items": [{"name": "first"}, {"name": "second"}]}, "total": 3}
    ctx = {"result": result}
    assert jsonpath_to_path("$.data.user.email") == "result.data.user.email"
    assert jsonpath_to_path("$.data.items[1].name") == "result.data.items.1.name" and jsonpath_to_path("$") == "result"
    assert jsonpath_to_path("$['total']") == "result.total"
    assert _eval("$.data.user.email", ctx) == "a@b.c" and _eval("$.data.items[1].name", ctx) == "second"
    assert _eval("$.total", ctx) == 3 and _eval("$.data.nothing", ctx) is None
    assert _eval("=$5", ctx) == "$5" and _eval("$5", ctx) is None                           # "$5" is not mistaken for a JSONPath
    assert _eval("result.data.user.email", ctx) == "a@b.c"                                 # the old form still works


def test_array_and_object_extraction_values():
    assert check_value({"type": "array"}, ["a", "b"]) == ["a", "b"] and check_value({"type": "array"}, '["x"]') == ["x"]
    assert check_value({"type": "array"}, "not json") is None and check_value({"type": "array"}, {"a": 1}) is None
    assert check_value({"type": "object"}, {"a": 1}) == {"a": 1} and check_value({"type": "object"}, '{"a": 1}') == {"a": 1}
    assert check_value({"type": "object"}, []) is None and check_value({"type": "array"}, []) is None


def test_importer_turns_jsonpath_outputs_into_result_paths():
    import copy
    from runtime.platform.hamsa_import import convert, parse
    from .test_projects import HAMSA
    h = copy.deepcopy(HAMSA)
    h["workflow"]["nodes"][2]["extractVariables"]["variables"] = [
        {"name": "patient_count", "extractionPrompt": "$.count"}, {"name": "first_name", "extractionPrompt": "$.items[0].name"}]
    g = Graph.from_dict(convert(parse(json.dumps({"success": True, "data": h})))["flow"])
    assert g.nodes["lookup_patient"].outputs == {"patient_count": "result.count", "first_name": "result.items.0.name"}


# ---------------------------------------------------------------- a call that starts with params

FLOW = """
start: hello
nodes:
  - {id: hello, type: conversation, say: {en: "Welcome to {{ business_name }}, visit {{ visits }}.", ar: "x"}}
"""


def run_chat(monkeypatch, params):
    from runtime.server import app as server_app
    from runtime.server import chat
    loop = asyncio.new_event_loop()
    agent = make_loaded(loop, "a", "Agent A", FLOW)
    agent.bundle = {"variables": {"business_name": {"type": "string", "default": "Cloud Clinic"}, "visits": {"type": "number", "default": 0}}}
    rt = TwoAgentRt({"a": agent}, EventBus())
    monkeypatch.setitem(server_app.state, "rt", rt)
    app = FastAPI()
    app.include_router(chat.router)
    out = []
    with TestClient(app) as c, c.websocket_connect("/ws/chat") as ws:
        start = {"event": "start", "agent": "a", "language": "en"}
        if params is not None:
            start["params"] = params
        ws.send_text(json.dumps(start))
        first = json.loads(ws.receive_text())
        out.append(first)
        if first["event"] == "ready":
            read_until(ws, "transcript")
            ws.send_text(json.dumps({"event": "text", "text": "hi"}))
            out.append(read_until(ws, "transcript", "Welcome"))
            ws.send_text(json.dumps({"event": "stop"}))
    loop.close()
    return out


def test_chat_call_uses_defaults_and_params(monkeypatch):
    assert run_chat(monkeypatch, None)[1]["text"] == "Welcome to Cloud Clinic, visit 0."
    assert run_chat(monkeypatch, {"business_name": "Acme", "visits": "5"})[1]["text"] == "Welcome to Acme, visit 5."


def test_chat_call_with_bad_params_is_refused(monkeypatch):
    out = run_chat(monkeypatch, {"visits": "many"})
    assert out == [{"event": "error", "message": "param 'visits': expected number"}]


def test_declared_variables_of_a_bundle():
    assert declared_variables(None) == {} and declared_variables({}) == {} and declared_variables({"variables": DECLARED}) == DECLARED
