"""Phase 12.3: several worker processes kept in step (config reloads, ending calls, shared live view, eval jobs)."""

import asyncio
import json

import pytest

from runtime.control.eval_jobs import EvalJobs
from runtime.control.live import LiveHub
from runtime.events import Event, EventType
from runtime.platform.sync import CONFIG, CONTROL, LIVE, MAX_PAYLOAD, ClusterSync, compact_event


def test_compact_event_fits_a_notify_payload():
    big = {"type": "agent.say", "call_id": "c1", "data": {"text": "كلام " * 3000, "n": 1}}
    msg = compact_event(big)
    assert len(msg.encode()) <= MAX_PAYLOAD
    d = json.loads(msg)["data"]
    assert d["text"].endswith("…") and d["n"] == 1
    huge = {"type": "x", "call_id": "c1", "data": {f"k{i}": "v" * 500 for i in range(40)}}
    assert json.loads(compact_event(huge))["data"] == {"truncated": True}


async def test_messages_from_this_worker_are_ignored():
    got = []

    async def on_config(msg):
        got.append(msg)
    sync = ClusterSync("postgres://unused", worker_id="w1", on_config=on_config)
    sync._on_notify(None, 0, CONFIG, json.dumps({"kind": "release", "origin": "w1"}))
    sync._on_notify(None, 0, CONFIG, json.dumps({"kind": "release", "origin": "w2"}))
    sync._on_notify(None, 0, CONFIG, "not json")
    sync._on_notify(None, 0, LIVE, json.dumps({"origin": "w2"}))          # live sharing is off: no handler
    await asyncio.sleep(0)
    assert got == [{"kind": "release", "origin": "w2"}]


# ---------------------------------------------------------------- Runtime behaviour with a fake sync


class FakeSync:
    def __init__(self):
        self.sent = []

    async def config_changed(self, kind, **detail):
        self.sent.append(("config", kind, detail))

    async def end_call(self, call_id):
        self.sent.append(("end", call_id))


class FakeCall:
    def __init__(self):
        self.ended = False

    async def end_from_console(self):
        self.ended = True


def runtime_with(sync, live=None):
    from runtime.app import Runtime
    rt = Runtime.__new__(Runtime)
    rt.sync, rt.live, rt.calls = sync, live, {}
    rt.reloads = 0

    async def reload():
        rt.reloads += 1
    rt.reload = reload
    return rt


async def test_config_change_reloads_here_and_tells_the_others():
    from runtime.app import Runtime
    sync = FakeSync()
    rt = runtime_with(sync)
    await Runtime.config_changed(rt, "provider", id="groq-llm")
    assert rt.reloads == 1 and sync.sent == [("config", "provider", {"id": "groq-llm"})]
    await Runtime._on_config(rt, {"kind": "release", "origin": "w2"})     # from another worker: reload, no echo
    assert rt.reloads == 2 and len(sync.sent) == 1


async def test_end_call_reaches_the_worker_that_owns_it():
    from runtime.app import Runtime
    sync, live = FakeSync(), LiveHub()
    rt = runtime_with(sync, live)
    local = rt.calls["here"] = FakeCall()
    assert await Runtime.end_call(rt, "here") and local.ended and sync.sent == []
    await live(Event(type=EventType.CALL_START, call_id="elsewhere", data={}))   # known from the shared live view
    assert await Runtime.end_call(rt, "elsewhere") and sync.sent == [("end", "elsewhere")]
    assert not await Runtime.end_call(rt, "nowhere")
    # the owning worker receives the request
    owner = runtime_with(FakeSync())
    remote = owner.calls["elsewhere"] = FakeCall()
    await Runtime._on_control(owner, {"action": "end", "call_id": "elsewhere"})
    assert remote.ended


async def test_live_view_includes_other_workers_calls():
    from runtime.app import Runtime
    live = LiveHub()
    rt = runtime_with(FakeSync(), live)
    ev = Event(type=EventType.TURN_START, call_id="ivr-remote", turn_id=2, data={"text": "أبي أحجز"})
    await Runtime._on_live(rt, {**ev.model_dump(mode="json"), "origin": "w2"})
    assert live.active["ivr-remote"].turns == 2 and live.active["ivr-remote"].last_user == "أبي أحجز"


async def test_eval_jobs_without_a_database(monkeypatch):
    jobs = EvalJobs()

    async def no_db():
        return None
    monkeypatch.setattr(jobs, "_pool", no_db)
    assert not await jobs.running()
    await jobs.create("job-1", 2, "w1")
    assert await jobs.running()
    await jobs.progress("job-1", {"case": "a", "passed": True})
    await jobs.finish("job-1", run_id="r1")
    assert await jobs.get("job-1") == {"status": "done", "total": 2, "done": [{"case": "a", "passed": True}],
                                       "run_id": "r1", "error": None}
    assert not await jobs.running()


# ---------------------------------------------------------------- real Postgres (skipped without one)


async def _db_ok() -> bool:
    try:
        from runtime.data.db import get_pool
        pool = await asyncio.wait_for(get_pool(), 5)
        async with pool.acquire() as c:
            await c.fetchval("SELECT 1")
        return True
    except Exception:
        return False


async def test_two_workers_over_postgres():
    if not await _db_ok():
        pytest.skip("no database")
    from runtime.config import get_settings
    s = get_settings()
    got_b: list = []
    ended_b: list = []
    live_b: list = []

    async def on_config(m):
        got_b.append(m)

    async def on_control(m):
        ended_b.append(m)

    async def on_live(m):
        live_b.append(m)
    a = ClusterSync(s.database_url.get_secret_value(), worker_id="test-a", on_config=lambda m: asyncio.sleep(0),
                    share_live=True)
    b = ClusterSync(s.database_url.get_secret_value(), worker_id="test-b", on_config=on_config,
                    on_control=on_control, on_live=on_live, share_live=True)
    await a.start()
    await b.start()
    try:
        await a.config_changed("release", agent="hmg-care")
        await a.end_call("ivr-123")
        await a(Event(type=EventType.AGENT_SAY, call_id="ivr-123", data={"text": "هلا"}))
        for _ in range(50):
            if got_b and ended_b and live_b:
                break
            await asyncio.sleep(0.05)
        assert got_b[0]["kind"] == "release" and got_b[0]["origin"] == "test-a"
        assert ended_b[0]["call_id"] == "ivr-123"
        assert live_b[0]["call_id"] == "ivr-123" and live_b[0]["data"]["text"] == "هلا"
    finally:
        await a.stop()
        await b.stop()
