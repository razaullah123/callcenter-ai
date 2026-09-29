import io
import json
import time

import pytest

from runtime.events import ConsoleSink, Event, EventBus, EventType, JsonlSink, Level


async def test_bound_emitter_fills_context_and_fans_out(tmp_path):
    bus = EventBus()
    seen: list[Event] = []

    async def collect(e: Event) -> None:
        seen.append(e)

    bus.subscribe(collect)
    bus.subscribe(JsonlSink(tmp_path))
    await bus.start()

    ev = bus.bind(call_id="c_1", skill="book_appointment")
    ev.bind(turn_id=3, step="slot").emit(EventType.TOOL_END, tool="get_available_slots", latency_ms=212)
    await bus.stop()

    assert len(seen) == 1
    e = seen[0]
    assert (e.call_id, e.turn_id, e.skill, e.step) == ("c_1", 3, "book_appointment", "slot")
    assert e.data == {"tool": "get_available_slots"} and e.latency_ms == 212

    [logfile] = tmp_path.glob("events-*.jsonl")
    row = json.loads(logfile.read_text(encoding="utf-8").strip())
    assert row["type"] == "tool.end" and row["call_id"] == "c_1"


async def test_redactor_runs_before_subscribers():
    bus = EventBus()
    seen: list[Event] = []

    async def collect(e: Event) -> None:
        seen.append(e)

    def mask(e: Event) -> Event:
        if "national_id" in e.data:
            e.data["national_id"] = "***"
        return e

    bus.add_redactor(mask)
    bus.subscribe(collect)
    await bus.start()
    bus.bind(call_id="c").emit(EventType.SLOT_SET, national_id="1012345678")
    await bus.stop()
    assert seen[0].data["national_id"] == "***"


async def test_failing_subscriber_does_not_break_others():
    bus = EventBus()
    seen: list[Event] = []

    async def boom(e: Event) -> None:
        raise RuntimeError("sink down")

    async def collect(e: Event) -> None:
        seen.append(e)

    bus.subscribe(boom)
    bus.subscribe(collect)
    await bus.start()
    bus.bind().emit(EventType.CALL_START)
    await bus.stop()
    assert len(seen) == 1


async def test_timed_emits_latency_and_errors():
    bus = EventBus()
    seen: list[Event] = []

    async def collect(e: Event) -> None:
        seen.append(e)

    bus.subscribe(collect)
    await bus.start()
    ev = bus.bind(call_id="c")
    with ev.timed(EventType.LLM_END, model="m"):
        time.sleep(0.02)  # asyncio.sleep can return early on Windows' coarse loop clock
    with pytest.raises(ValueError):
        with ev.timed(EventType.TOOL_END, tool="x"):
            raise ValueError("bad")
    await bus.stop()

    assert seen[0].type == EventType.LLM_END and seen[0].latency_ms >= 10
    assert seen[1].type == EventType.ERROR and seen[1].level == Level.ERROR
    assert seen[1].data["during"] == "tool.end"


async def test_console_sink_respects_level():
    buf = io.StringIO()
    sink = ConsoleSink(min_level=Level.WARNING, stream=buf)
    await sink(Event(type=EventType.TURN_START))
    await sink(Event(type=EventType.ERROR, level=Level.ERROR, call_id="c_9", data={"msg": "خطأ"}))
    out = buf.getvalue()
    assert "turn.start" not in out and "error" in out and "خطأ" in out


async def test_live_hub_pushes_active_calls_when_a_call_starts_and_ends():
    import json
    from runtime.control.live import LiveHub
    from runtime.events import Event, EventType
    hub = LiveHub()
    q = hub.subscribe()
    await hub(Event(type=EventType.CALL_START, call_id="c1", data={"language": "ar"}))
    msgs = [json.loads(q.get_nowait()) for _ in range(q.qsize())]
    assert [m["kind"] for m in msgs] == ["event", "active"] and msgs[-1]["calls"][0]["call_id"] == "c1"
    await hub(Event(type=EventType.TURN_START, call_id="c1", turn_id=1, data={"text": "hi"}))
    assert [json.loads(q.get_nowait())["kind"] for _ in range(q.qsize())] == ["event"]   # no change, no list
    await hub(Event(type=EventType.CALL_END, call_id="c1"))
    msgs = [json.loads(q.get_nowait()) for _ in range(q.qsize())]
    assert msgs[-1] == {"kind": "active", "calls": []}


async def test_late_events_do_not_revive_an_ended_call():
    from runtime.control.live import LiveHub
    from runtime.events import Event, EventType
    hub = LiveHub()
    await hub(Event(type=EventType.CALL_START, call_id="c1"))
    await hub(Event(type=EventType.CALL_END, call_id="c1", data={"reason": "caller_hangup"}))
    await hub(Event(type=EventType.TURN_END, call_id="c1", turn_id=3))       # the cancelled reply's last event
    assert hub.active_calls() == []
