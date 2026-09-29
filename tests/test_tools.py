import asyncio

import pytest

from runtime.events import Event, EventBus, EventType
from runtime.providers import ToolCall
from runtime.tools import MockMCP, ToolContext, ToolError
from runtime.tools.factory import build_tooling


@pytest.fixture
async def env():
    bus = EventBus()
    events: list[Event] = []

    async def collect(e):
        events.append(e)

    bus.subscribe(collect)
    await bus.start()
    mock = MockMCP()
    executor = await build_tooling(mock)
    yield executor, mock, bus.bind(call_id="c1"), events, bus
    await bus.stop()


def ctx(**kw):
    return ToolContext(**{"call_id": "c1", "language_id": 1, "patient_id": 4455, **kw})


def call(name, **args):
    return ToolCall(id="t1", name=name, arguments=args)


async def test_catalog_has_46_mcp_plus_local_tools(env):
    executor, *_ = env
    tools = executor.catalog.tools
    assert sum(t.source == "mcp" for t in tools.values()) == 46
    assert {"find_hospital_by_name", "resolve_location"} <= {n for n, t in tools.items() if t.source == "local"}
    assert "postgres_execute_query" not in tools


async def test_llm_schema_hides_injected_params(env):
    executor, *_ = env
    [spec] = executor.llm_tools(["api_book_Appointment"])
    params = spec["function"]["parameters"]
    assert "PatientID" not in params["properties"] and "LanguageID" not in params["properties"]
    assert "PatientID" not in params["required"] and "DoctorID" in params["required"]
    # Catalog schema itself is untouched
    assert "PatientID" in executor.catalog.get("api_book_Appointment").input_schema["properties"]


async def test_injects_session_values_and_coerces_types(env):
    executor, mock, ev, _, _ = env
    mock.on("mssql_get_clinics_for_project", [{"ClinicID": 10}])
    r = await executor.execute(call("mssql_get_clinics_for_project", projectId="12", LanguageID=2), ctx(), ev)
    assert r.ok
    assert mock.calls[-1] == ("mssql_get_clinics_for_project", {"projectId": 12, "LanguageID": 1})  # LLM can't override


async def test_patient_tools_require_verified_caller(env):
    executor, mock, ev, _, _ = env
    r = await executor.execute(call("api_get_insurance_detail"), ctx(patient_id=None), ev)
    assert not r.ok and "not verified" in r.error and mock.calls == []


async def test_validation_error_goes_back_to_llm(env):
    executor, mock, ev, _, _ = env
    r = await executor.execute(call("mssql_get_clinics_for_project", projectId="abc"), ctx(), ev)
    assert not r.ok and "invalid arguments" in r.error and mock.calls == []


async def test_skill_restriction_and_unknown_tool(env):
    executor, _, ev, _, _ = env
    r = await executor.execute(call("api_create_complaint", projectId=1), ctx(allowed_tools={"mssql_get_upcoming_appointment"}), ev)
    assert not r.ok and "not available" in r.error
    r = await executor.execute(call("postgres_execute_query", query="select 1"), ctx(), ev)
    assert not r.ok and "unknown tool" in r.error


async def test_read_cache(env):
    executor, mock, ev, _, _ = env
    mock.on("mssql_get_clinics_for_project", [{"ClinicID": 10}])
    c = call("mssql_get_clinics_for_project", projectId=12)
    first, second = await executor.execute(c, ctx(), ev), await executor.execute(c, ctx(), ev)
    assert not first.cached and second.cached and len(mock.calls) == 1


async def test_idempotent_booking_never_runs_twice_per_call(env):
    executor, mock, ev, _, _ = env
    mock.on("api_book_Appointment", {"AppointmentNo": 991})
    c = call("api_book_Appointment", ProjectID=12, ClinicID=10, DoctorID=5, StartTime="10:00",
             StrAppointmentDate="2026-10-01")
    a = await executor.execute(c, ctx(), ev)
    b = await executor.execute(c, ctx(), ev)
    assert a.ok and b.ok and b.cached and len(mock.calls) == 1
    other_call = await executor.execute(c, ctx(call_id="c2"), ev)
    assert not other_call.cached and len(mock.calls) == 2


async def test_reads_retry_once_writes_never(env):
    executor, mock, ev, _, _ = env
    attempts = {"n": 0}

    def flaky(args):
        attempts["n"] += 1
        return ConnectionError("reset") if attempts["n"] == 1 else [{"ok": 1}]

    mock.on("mssql_get_upcoming_appointment", flaky)
    assert (await executor.execute(call("mssql_get_upcoming_appointment"), ctx(), ev)).ok
    mock.on("mssql_cancel_appointment", lambda a: ConnectionError("reset"))
    r = await executor.execute(call("mssql_cancel_appointment", appointment_no=1, project_id=12), ctx(), ev)
    assert not r.ok and sum(1 for n, _ in mock.calls if n == "mssql_cancel_appointment") == 1


async def test_timeout_is_reported(env, monkeypatch):
    executor, mock, ev, _, _ = env

    async def slow(name, args, timeout_s):
        await asyncio.sleep(1)

    monkeypatch.setattr(mock, "call", slow)
    tool = executor.catalog.tools["mssql_get_upcoming_appointment"]
    monkeypatch.setitem(executor.catalog.tools, tool.name, type(tool)(**{**tool.__dict__, "timeout_s": 0.05}))
    r = await executor.execute(call("mssql_get_upcoming_appointment"), ctx(), ev)
    assert not r.ok and r.error == "timeout"


async def test_pre_hook_can_block(env):
    executor, mock, ev, _, _ = env

    async def confirm_gate(tool, args, c):
        if tool.kind == "write" and not c.extra.get("confirmed"):
            raise ToolError("ask the caller to confirm first")

    executor.pre_hooks.append(confirm_gate)
    r = await executor.execute(call("mssql_confirm_appointment", appointment_no=1, project_id=12), ctx(), ev)
    assert not r.ok and "confirm" in r.error and mock.calls == []


async def test_events_emitted(env):
    executor, mock, ev, events, bus = env
    mock.on("mssql_get_clinics_for_project", [{"ClinicID": 10}])
    await executor.execute(call("mssql_get_clinics_for_project", projectId=12), ctx(), ev)
    await executor.execute(call("nope"), ctx(), ev)
    await bus.stop()
    types_ = [e.type for e in events]
    assert types_.count(EventType.TOOL_START) == 2 and EventType.TOOL_END in types_ and EventType.TOOL_ERROR in types_
    end = next(e for e in events if e.type == EventType.TOOL_END)
    assert end.call_id == "c1" and end.latency_ms is not None


async def test_result_summarized_for_llm(env):
    executor, mock, ev, _, _ = env
    mock.on("api_get_insurance_detail", {"IsActive": True, "Empty": None, "Rows": list(range(40))})
    r = await executor.execute(call("api_get_insurance_detail"), ctx(), ev)
    assert "Empty" not in r.content and '"_more":25' in r.content and len(r.data["Rows"]) == 40
