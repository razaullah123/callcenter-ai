"""Hamsa parity (Batch Calls): CSV recipients, schedule + daily window, the dialer, controls, the console API."""

import asyncio
import base64
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest

from runtime.config import get_settings
from runtime.platform import batch
from runtime.platform.batch import BatchDialer
from runtime.platform.store import MemoryStore

from .test_projects import console  # noqa: F401 — the fixture

UTC = timezone.utc
CFG = {"send_type": "now", "timezone": "Asia/Riyadh", "window_start": "09:00", "window_end": "18:00",
       "days": [6, 0, 1, 2, 3]}                          # Riyadh is UTC+3; day 0 = Monday
MON_NOON = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)      # Monday 12:00 in Riyadh
MON_NIGHT = datetime(2026, 10, 5, 20, 0, tzinfo=UTC)    # Monday 23:00 in Riyadh
FRI_NOON = datetime(2026, 10, 9, 9, 0, tzinfo=UTC)      # Friday 12:00 (not a working day in CFG)


# ---------------------------------------------------------------- recipients

def test_phone_rules():
    ok = lambda s, ignore=False: batch.check_phone(s, ignore)      # noqa: E731
    assert ok("+966548802968") == ("+966548802968", []) and ok("966548802968")[0] == "+966548802968"
    for bad in ("(202) 555-1234", "abc123", "", "12345"):
        assert ok(bad)[1], bad
    assert "at least 7" in ok("12345")[1][0] and "E.164" in ok("0123456789")[1][0]
    assert ok("0123456789", ignore=True) == ("0123456789", [])           # ignoreE164Validation
    assert ok("123", ignore=True)[1]                                      # still needs 7 digits


def test_csv_columns_variables_and_errors():
    raw = ("phoneNumber,name,city,ignoreE164Validation\n+966548802968,Sara,Riyadh,\n"
           "0551234567,Omar,Jeddah,true\n\n(1) 2,Bad,X,\n").encode()
    rows = batch.parse_csv(raw)
    assert [r["row"] for r in rows] == [2, 3, 5]                           # the blank line is skipped, rows keep their line
    assert rows[0]["variables"] == {"city": "Riyadh"} and rows[0]["name"] == "Sara" and rows[1]["ignore_e164"]
    checked = batch.check_rows(rows)
    assert [r["phone"] for r in checked["accepted"]] == ["+966548802968", "0551234567"]
    assert checked["rejected"][0]["row"] == 5 and checked["rejected"][0]["errors"]
    assert batch.check_rows([{"phone": "+966548802968"}, {"phone": "966548802968"}])["duplicates"] == 1
    assert batch.parse_csv(b"phone_number\n+966548802968\n")[0]["phone"] == "+966548802968"
    for bad, msg in ((b"", "empty"), (b"name\nx\n", "phoneNumber"), (b"phoneNumber\n", "no recipients")):
        with pytest.raises(ValueError, match=msg):
            batch.parse_csv(bad)
    with pytest.raises(ValueError, match="larger than 50 MB"):
        batch.parse_csv(b"x" * (batch.MAX_CSV_BYTES + 1))


def test_variables_for_the_call_ignore_what_does_not_fit():
    declared = {"age": {"type": "number"}, "city": {"type": "string"}}
    rec = {"name": "Sara", "variables": {"city": "Riyadh", "age": "forty", "First Name": "x", "extra": "kept"}}
    # `age` isn't a number → left out; `First Name` isn't a valid variable name → left out; nothing raises
    assert batch.call_params(declared, rec) == {"name": "Sara", "city": "Riyadh", "extra": "kept"}
    assert batch.call_params(declared, {"variables": {"age": "41"}}) == {"age": "41"}


# ---------------------------------------------------------------- schedule

def test_config_validation():
    assert batch.config_errors(CFG) == []
    future = (datetime.now(UTC) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
    assert batch.config_errors({**CFG, "send_type": "schedule", "scheduled_at": future}) == []
    errs = lambda **kw: " | ".join(batch.config_errors({**CFG, **kw}))        # noqa: E731
    assert "in the past" in errs(send_type="schedule", scheduled_at="2020-01-01T09:00")
    assert "date and time" in errs(send_type="schedule", scheduled_at="soon")
    assert "unknown timezone" in errs(timezone="Mars/Base")
    assert "HH:MM" in errs(window_start="9am") and "differ" in errs(window_end="09:00")
    assert "day" in errs(days=[]) and "day" in errs(days=[7])
    assert "send_type" in errs(send_type="later")


def test_the_daily_window():
    assert batch.in_window(MON_NOON, CFG) and not batch.in_window(MON_NIGHT, CFG)
    assert not batch.in_window(FRI_NOON, CFG)                                  # a day that isn't allowed
    assert not batch.in_window(datetime(2026, 10, 5, 15, 0, tzinfo=UTC), CFG)  # 18:00 sharp is outside
    assert batch.in_window(datetime(2026, 10, 5, 14, 59, tzinfo=UTC), CFG)
    night = {**CFG, "window_start": "22:00", "window_end": "06:00"}            # past midnight: belongs to the day it began
    assert batch.in_window(MON_NIGHT, night)                                   # Mon 23:00
    assert batch.in_window(datetime(2026, 10, 5, 22, 0, tzinfo=UTC), {**night, "days": [0]})          # Tue 01:00 (Mon's window)
    assert not batch.in_window(datetime(2026, 10, 6, 22, 0, tzinfo=UTC), {**night, "days": [0]})      # Wed 01:00


def test_scheduled_start_uses_the_batch_timezone():
    cfg = {**CFG, "send_type": "schedule", "scheduled_at": "2026-10-08T09:00"}
    assert batch.scheduled_utc(cfg) == datetime(2026, 10, 8, 6, 0, tzinfo=UTC)
    assert batch.scheduled_utc({**cfg, "scheduled_at": "2026-10-08T09:00+00:00"}) == datetime(2026, 10, 8, 9, 0, tzinfo=UTC)
    assert batch.scheduled_utc(CFG) is None


def test_tokens_are_signed_and_scoped():
    s = get_settings()
    t = batch.make_token(s, "bc_1", 7, 60)
    assert batch.read_token(s, t) == ("bc_1", 7)
    assert batch.read_token(s, t + "x") is None and batch.read_token(s, "nope") is None
    assert batch.read_token(s, batch.make_token(s, "bc_1", 7, -5)) is None


# ---------------------------------------------------------------- the dialer

class World:
    """A dialer over a MemoryStore, a fake dial URL and a fake clock."""

    def __init__(self, live=True, **settings):
        self.store = MemoryStore()
        s = get_settings().model_copy(update={"batch_live_dial": live, "public_base_url": "https://agent.example.com",
                                              **settings})
        self.rt = SimpleNamespace(platform=self.store, settings=s, secrets=None)
        self.dialer = BatchDialer(self.rt)
        self.posts: list[dict] = []
        self.answer = lambda body: httpx.Response(200, json={"ok": True})

        def handler(request: httpx.Request) -> httpx.Response:
            import json
            body = json.loads(request.content)
            self.posts.append({"url": str(request.url), "auth": request.headers.get("authorization"), **body})
            return self.answer(body)
        self.dialer._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def batch(self, n=3, cfg=None, **kw) -> str:
        await self.store.put_outbound_number({"workspace_id": "hmg", "number": "+966112000000", "dial_url": "https://pbx.example.com/dial"})
        bid = batch.new_id()
        await self.store.put_batch({"id": bid, "workspace_id": "hmg", "name": "B", "agent_id": "hmg-care",
                                    "from_number": "+966112000000", "status": "scheduled", "config": {**CFG, **(cfg or {})}, **kw})
        await self.store.add_recipients(bid, [{"phone": f"+96655000000{i}", "name": f"N{i}", "variables": {"city": "Riyadh"}}
                                              for i in range(n)])
        return bid

    async def statuses(self, bid) -> dict:
        return await self.store.recipient_counts(bid)


async def test_nothing_is_dialed_unless_live():
    w = World(live=False)
    bid = await w.batch()
    await w.dialer.tick(MON_NOON)
    assert w.posts == [] and await w.statuses(bid) == {"completed": 3}
    assert (await w.store.batch(bid))["status"] == "completed"
    rows, _ = await w.store.recipients(bid)
    assert "simulated" in rows[0]["error"]


async def test_a_live_dial_posts_everything_the_pbx_needs():
    w = World()
    bid = await w.batch(1)
    await w.dialer.tick(MON_NOON)
    (post,) = w.posts
    assert post["url"] == "https://pbx.example.com/dial" and post["to"] == "+966550000000" and post["from"] == "+966112000000"
    assert post["name"] == "N0" and post["variables"] == {"city": "Riyadh"} and post["batch_call_id"] == bid
    assert post["ws_url"] == "wss://agent.example.com/ws/voice-pipeline" and post["status_url"].endswith("/api/outbound/status")
    assert batch.read_token(w.rt.settings, post["outbound_token"]) == (bid, post["recipient_id"])
    assert await w.statuses(bid) == {"in_progress": 1} and (await w.store.batch(bid))["status"] == "running"


async def test_calls_wait_for_the_window_and_the_start_time():
    w = World()
    bid = await w.batch(cfg={"send_type": "schedule", "scheduled_at": "2026-10-05T14:00"})        # Mon 14:00 Riyadh = 11:00 UTC
    await w.dialer.tick(MON_NOON)                                                                   # 09:00 UTC: not yet
    assert (await w.store.batch(bid))["status"] == "scheduled" and w.posts == []
    await w.dialer.tick(MON_NOON + timedelta(hours=2))                                              # 11:00 UTC: it starts
    assert (await w.store.batch(bid))["status"] == "running" and len(w.posts) == 3
    w2 = World()
    bid2 = await w2.batch()
    await w2.dialer.tick(MON_NIGHT)                                                                 # outside the daily window
    assert (await w2.store.batch(bid2))["status"] == "running" and w2.posts == [] and await w2.statuses(bid2) == {"pending": 3}
    await w2.dialer.tick(FRI_NOON)                                                                  # not an allowed day
    assert w2.posts == []
    await w2.dialer.tick(MON_NOON + timedelta(days=7))                                              # deferred, not skipped
    assert len(w2.posts) == 3


async def test_concurrency_is_shared_and_calls_start_as_capacity_frees():
    w = World(batch_max_concurrent=2)
    a, b = await w.batch(3), await w.batch(2)
    await w.dialer.tick(MON_NOON)
    assert len(w.posts) == 2 and (await w.statuses(a)) == {"in_progress": 2, "pending": 1} and await w.statuses(b) == {"pending": 2}
    first = w.posts[0]["recipient_id"]
    await w.dialer.on_connect(first, "ivr-1")
    await w.dialer.on_end(first, "ivr-1", 0, "hangup")
    await w.dialer.tick(MON_NOON)
    assert len(w.posts) == 3                                                                        # one slot opened, oldest batch first
    assert (await w.statuses(a)) == {"completed": 1, "in_progress": 2}


async def test_a_call_that_connects_completes_and_the_batch_finishes():
    w = World()
    bid = await w.batch(2)
    await w.dialer.tick(MON_NOON)
    for post in w.posts:
        await w.dialer.on_connect(post["recipient_id"], f"ivr-{post['recipient_id']}")
        await w.dialer.on_end(post["recipient_id"], f"ivr-{post['recipient_id']}", 0, "hangup")
    await w.dialer.tick(MON_NOON)
    assert await w.statuses(bid) == {"completed": 2} and (await w.store.batch(bid))["status"] == "completed"
    rows, _ = await w.store.recipients(bid)
    assert rows[0]["call_id"].startswith("ivr-") and rows[0]["duration_s"] is not None


async def test_unanswered_busy_and_failed_calls():
    w = World(batch_ring_timeout_s=60)
    bid = await w.batch(4)
    await w.dialer.tick(MON_NOON)
    ids = [p["recipient_id"] for p in w.posts]
    assert await w.dialer.report(ids[0], "busy") and await w.dialer.report(ids[1], "failed", "SIP 503")
    assert not await w.dialer.report(ids[0], "failed")                                              # already settled
    await w.dialer.on_connect(ids[2], "ivr-x")
    assert not await w.dialer.report(ids[2], "no_answer")                                           # it connected: a report is stale
    await w.dialer.tick(MON_NOON + timedelta(seconds=120))                                          # ids[3] never connected
    rows = {r["id"]: r for r in (await w.store.recipients(bid))[0]}
    assert (rows[ids[0]]["status"], rows[ids[0]]["error"]) == ("no_answer", "the line was busy")
    assert (rows[ids[1]]["status"], rows[ids[1]]["error"]) == ("failed", "SIP 503")
    assert rows[ids[2]]["status"] == "in_progress" and rows[ids[3]]["status"] == "no_answer"
    await w.dialer.tick(MON_NOON + timedelta(hours=3))                                              # connected but never ended
    assert (await w.store.recipient(ids[2]))["status"] == "failed"
    assert (await w.store.batch(bid))["status"] == "failed"                                         # nobody was reached


async def test_dial_url_problems_fail_the_recipient_with_the_reason():
    w = World()
    bid = await w.batch(2)
    w.answer = lambda body: httpx.Response(502)
    await w.dialer.tick(MON_NOON)
    rows, _ = await w.store.recipients(bid)
    assert [r["status"] for r in rows] == ["failed", "failed"] and "HTTP 502" in rows[0]["error"]
    w2 = World()
    bid2 = await w2.batch(1)
    w2.answer = lambda body: httpx.Response(200, json={"status": "busy"})                          # an immediate answer from the PBX
    await w2.dialer.tick(MON_NOON)
    assert (await w2.store.recipients(bid2))[0][0]["status"] == "no_answer"
    w3 = World()
    bid3 = await w3.batch(1)
    await w3.store.delete_outbound_number("hmg", "+966112000000")
    await w3.dialer.tick(MON_NOON)
    assert "not set up" in (await w3.store.recipients(bid3))[0][0]["error"] and w3.posts == []


async def test_the_dial_token_is_a_secret_sent_as_bearer():
    w = World()
    await w.batch(0)
    await w.store.put_outbound_number({"workspace_id": "hmg", "number": "+966112000000", "dial_url": "https://pbx.example.com/dial",
                                      "dial_auth": {"secret": "DIAL_TOKEN_966112000000"}})

    class Secrets:
        async def resolve(self, ref):
            return "s3cret"
    w.rt.secrets = Secrets()
    bid = await w.batch(1)
    await w.dialer.tick(MON_NOON)
    assert w.posts[0]["auth"] == "Bearer s3cret"


async def test_paused_and_cancelled_batches_place_no_calls():
    w = World()
    a = await w.batch(2, status="paused")
    b = await w.batch(2, status="cancelled")
    await w.dialer.tick(MON_NOON)
    assert w.posts == [] and await w.statuses(a) == {"pending": 2} and await w.statuses(b) == {"pending": 2}


# ---------------------------------------------------------------- the console API

@pytest.fixture
def api(console):  # noqa: F811
    c, rt, store, loop = console
    from runtime.control import batch_api
    c.app.include_router(batch_api.router)
    rt.batch = BatchDialer(rt)
    rt.settings = rt.settings.model_copy(update={"batch_live_dial": False})
    loop.run_until_complete(store.set_published if False else asyncio.sleep(0))
    return c, rt, store, loop


def csv64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


def published_agent(store, loop) -> str:
    async def find():
        return next(a["id"] for a in await store.agents("hmg") if a.get("published_release_id"))
    return loop.run_until_complete(find())


def test_outbound_numbers(api):
    c, rt, store, loop = api
    assert c.get("/api/outbound-numbers").json() == []
    r = c.put("/api/outbound-numbers", json={"number": "+966 11 200 0000", "dial_url": "https://pbx.example.com/dial"})
    assert r.status_code == 422                                           # spaces: digits only
    assert c.put("/api/outbound-numbers", json={"number": "+966112000000", "dial_url": "ftp://x"}).status_code == 422
    assert c.put("/api/outbound-numbers", json={"number": "+966112000000", "label": "Main", "dial_url": "https://pbx.example.com/dial"}).status_code == 200
    assert c.put("/api/outbound-numbers", json={"number": "+966112000000", "dial_url": "https://pbx.example.com/d", "dial_token": "t"}).status_code == 503   # no secret store in the test runtime
    (n,) = c.get("/api/outbound-numbers").json()
    assert n == {"number": "+966112000000", "label": None, "dial_url": "https://pbx.example.com/d", "has_token": False} or n["dial_url"].endswith("/dial")
    assert c.delete("/api/outbound-numbers/+966112000000").status_code == 200 and c.get("/api/outbound-numbers").json() == []


def test_validate_then_create_then_control(api):
    c, rt, store, loop = api
    agent = published_agent(store, loop)
    c.put("/api/outbound-numbers", json={"number": "+966112000000", "dial_url": "https://pbx.example.com/dial"})
    csv = "phone_number,name,city\n+966548802968,Sara,Riyadh\n12345,Bad,X\n+966551234567,Omar,Jeddah\n"
    v = c.post("/api/batch-calls/validate", json={"csv_base64": csv64(csv)}).json()
    assert len(v["accepted"]) == 2 and len(v["rejected"]) == 1 and v["variables"] == ["city"]
    assert c.post("/api/batch-calls/validate", json={"csv_base64": csv64("name\nx\n")}).status_code == 422
    # the rejected row is fixed in the console and sent back as rows
    fixed = [{"phone": "+966548802968", "name": "Sara", "variables": {"city": "Riyadh"}},
             {"phone": "+966551234567", "name": "Omar", "variables": {}}]
    body = {"name": "Campaign", "agent_id": agent, "from_number": "+966112000000", "rows": fixed,
            "config": {"send_type": "now", "timezone": "Asia/Riyadh", "window_start": "09:00", "window_end": "18:00", "days": [0, 1, 2, 3, 4, 6]}}
    assert c.post("/api/batch-calls", json={**body, "rows": v["accepted"] + [{"phone": "12345"}]}).status_code == 422     # an invalid row
    assert c.post("/api/batch-calls", json={**body, "from_number": "+966999"}).status_code == 422
    assert c.post("/api/batch-calls", json={**body, "config": {**body["config"], "days": []}}).status_code == 422
    assert c.post("/api/batch-calls", json={**body, "agent_id": "nope"}).status_code == 404
    made = c.post("/api/batch-calls", json=body)
    assert made.status_code == 200, made.text
    bid = made.json()["id"]
    assert made.json()["recipients"] == 2
    row = c.get("/api/batch-calls").json()[0]
    assert row["id"] == bid and row["total"] == 2 and row["status"] == "scheduled" and row["progress"] == 0 and row["agent_name"]
    # pause / resume / cancel / rename
    assert c.post(f"/api/batch-calls/{bid}/actions/resume").status_code == 409            # not paused
    loop.run_until_complete(store.put_batch({"id": bid, "status": "running"}))
    assert c.post(f"/api/batch-calls/{bid}/actions/pause").json()["status"] == "paused"
    assert c.post(f"/api/batch-calls/{bid}/actions/pause").status_code == 409
    assert c.post(f"/api/batch-calls/{bid}/actions/resume").json()["status"] == "running"
    assert c.patch(f"/api/batch-calls/{bid}", json={"name": "  Renamed "}).json()["name"] == "Renamed"
    assert c.post(f"/api/batch-calls/{bid}/actions/retry").status_code == 409             # still running
    assert c.post(f"/api/batch-calls/{bid}/actions/cancel").json()["status"] == "cancelled"
    assert c.post(f"/api/batch-calls/{bid}/actions/explode").status_code == 404
    # the number is in use only while the batch is unfinished
    assert c.delete("/api/outbound-numbers/+966112000000").status_code == 200
    assert c.delete(f"/api/batch-calls/{bid}").status_code == 200 and c.get(f"/api/batch-calls/{bid}").status_code == 404


def test_recipients_list_filter_add_remove_and_retry(api):
    c, rt, store, loop = api
    agent = published_agent(store, loop)
    c.put("/api/outbound-numbers", json={"number": "+966112000000", "dial_url": "https://pbx.example.com/dial"})
    rows = [{"phone": f"+96655000000{i}", "name": f"Name{i}"} for i in range(5)]
    cfg = {"send_type": "now", "timezone": "Asia/Riyadh", "window_start": "00:00", "window_end": "23:59", "days": [0, 1, 2, 3, 4, 5, 6]}
    bid = c.post("/api/batch-calls", json={"name": "B", "agent_id": agent, "from_number": "+966112000000", "rows": rows, "config": cfg}).json()["id"]
    got = c.get(f"/api/batch-calls/{bid}/recipients", params={"q": "name3"}).json()
    assert got["total"] == 1 and got["items"][0]["phone"] == "+966550000003" and got["counts"] == {"pending": 5}
    assert c.get(f"/api/batch-calls/{bid}/recipients", params={"status": "nope"}).status_code == 422
    assert c.post(f"/api/batch-calls/{bid}/recipients", json={"csv_base64": csv64("phoneNumber\n+966540000001\n")}).json()["added"] == 1
    assert c.post(f"/api/batch-calls/{bid}/recipients", json={"rows": [{"phone": "bad"}]}).status_code == 422
    first = c.get(f"/api/batch-calls/{bid}/recipients").json()["items"][0]["id"]
    assert c.delete(f"/api/batch-calls/{bid}/recipients/{first}").status_code == 200
    assert c.get(f"/api/batch-calls/{bid}/recipients").json()["total"] == 5
    # run it (simulated: nobody is called) → completed, then retry has nothing to do
    loop.run_until_complete(rt.batch.tick())
    assert c.get(f"/api/batch-calls/{bid}").json()["status"] == "completed"
    assert c.post(f"/api/batch-calls/{bid}/actions/retry").status_code == 409
    assert c.post(f"/api/batch-calls/{bid}/recipients", json={"rows": rows[:1]}).status_code == 409        # finished
    # two calls fail → retry brings back only those
    ids = [r["id"] for r in c.get(f"/api/batch-calls/{bid}/recipients").json()["items"][:2]]
    for i, status in zip(ids, ("failed", "no_answer")):
        loop.run_until_complete(store.update_recipient(i, {"status": status, "error": "x"}))
    r = c.post(f"/api/batch-calls/{bid}/actions/retry").json()
    assert r == {"id": bid, "status": "scheduled", "retrying": 2}
    assert c.get(f"/api/batch-calls/{bid}/recipients").json()["counts"] == {"pending": 2, "completed": 3}


def test_batch_calls_need_a_published_agent_and_stay_in_their_project(api):
    c, rt, store, loop = api
    c.put("/api/outbound-numbers", json={"number": "+966112000000", "dial_url": "https://pbx.example.com/dial"})
    agent = published_agent(store, loop)
    cfg = {"send_type": "now", "timezone": "Asia/Riyadh", "window_start": "09:00", "window_end": "18:00", "days": [0]}
    bid = c.post("/api/batch-calls", json={"name": "B", "agent_id": agent, "from_number": "+966112000000",
                                           "rows": [{"phone": "+966548802968"}], "config": cfg}).json()["id"]
    assert c.post("/api/projects", json={"name": "Clinic"}).status_code == 200
    assert c.get(f"/api/batch-calls/{bid}", headers={"X-Project": "clinic"}).status_code in (403, 404)
    assert c.get("/api/batch-calls", headers={"X-Project": "clinic"}).json() == []


def test_the_pbx_reports_through_a_signed_token(api):
    c, rt, store, loop = api
    agent = published_agent(store, loop)
    w_cfg = {"send_type": "now", "timezone": "Asia/Riyadh", "window_start": "09:00", "window_end": "18:00", "days": [0]}
    c.put("/api/outbound-numbers", json={"number": "+966112000000", "dial_url": "https://pbx.example.com/dial"})
    bid = c.post("/api/batch-calls", json={"name": "B", "agent_id": agent, "from_number": "+966112000000",
                                           "rows": [{"phone": "+966548802968"}], "config": w_cfg}).json()["id"]
    rid = c.get(f"/api/batch-calls/{bid}/recipients").json()["items"][0]["id"]
    loop.run_until_complete(store.update_recipient(rid, {"status": "in_progress"}))
    token = batch.make_token(rt.settings, bid, rid, 60)
    assert c.post("/api/outbound/status", json={"outbound_token": "forged", "status": "busy"}).status_code == 401
    assert c.post("/api/outbound/status", json={"outbound_token": token, "status": "teleported"}).status_code == 422
    assert c.post("/api/outbound/status", json={"outbound_token": token, "status": "busy"}).json() == {"recorded": True}
    assert loop.run_until_complete(store.recipient(rid))["status"] == "no_answer"


# ---------------------------------------------------------------- phone numbers (Hamsa parity): one call, labels

def test_make_one_outbound_call(api):
    c, rt, store, loop = api
    agent = published_agent(store, loop)
    c.put("/api/outbound-numbers", json={"number": "+966112000000", "dial_url": "https://pbx.example.com/dial"})
    body = {"agent_id": agent, "from_number": "+966112000000", "to_number": "+966548802968", "params": {"customer_name": "Sara"}}
    assert c.post("/api/outbound-calls", json={**body, "to_number": "0548802968"}).status_code == 422          # not E.164
    assert c.post("/api/outbound-calls", json={**body, "from_number": "+966999"}).status_code == 422
    assert c.post("/api/outbound-calls", json={**body, "agent_id": "nope"}).status_code == 404

    r = c.post("/api/outbound-calls", json=body)
    assert r.status_code == 200, r.text
    bid = r.json()["batch_call_id"]
    got = c.get(f"/api/batch-calls/{bid}").json()
    assert got["name"] == "Call to +966548802968" and got["config"]["always"] and got["total"] == 1
    (rec,) = c.get(f"/api/batch-calls/{bid}/recipients").json()["items"]
    assert rec["phone"] == "+966548802968" and rec["variables"] == {"customer_name": "Sara"}
    loop.run_until_complete(rt.batch.tick())                      # runs at any hour: no daily window
    assert c.get(f"/api/batch-calls/{bid}").json()["status"] == "completed"                                   # simulated


def test_a_single_call_ignores_the_daily_window():
    cfg = {"send_type": "now", "timezone": "UTC", "always": True}
    assert batch.config_errors(cfg) == [] and batch.in_window(MON_NIGHT, cfg) and batch.in_window(FRI_NOON, cfg)


def test_route_label_is_kept_edited_and_cleared(console):  # noqa: F811
    c, rt, store, loop = console
    agent = next(r["agent_id"] for r in c.get("/api/routes").json())
    assert c.put("/api/routes", json={"pattern": "+966112345678", "agent_id": agent, "label": "Support line"}).status_code == 200
    row = lambda: next(r for r in c.get("/api/routes").json() if r["pattern"] == "+966112345678")      # noqa: E731
    assert row()["label"] == "Support line" and row()["created_at"]
    created = row()["created_at"]
    c.put("/api/routes", json={"pattern": "+966112345678", "agent_id": agent})                           # label left out: kept
    assert row()["label"] == "Support line" and row()["created_at"] == created
    c.put("/api/routes", json={"pattern": "+966112345678", "agent_id": agent, "label": ""})              # cleared
    assert not row()["label"]
    assert c.put("/api/routes", json={"pattern": "+966112345678", "agent_id": agent, "label": "x" * 101}).status_code == 422


def test_phone_test_calls_the_draft(api):
    c, rt, store, loop = api
    agent = published_agent(store, loop)
    c.put("/api/outbound-numbers", json={"number": "+966112000000", "dial_url": "https://pbx.example.com/dial"})
    body = {"agent_id": agent, "from_number": "+966112000000", "to_number": "+966548802968", "draft": True}
    assert c.post("/api/outbound-calls", json=body).status_code == 409                     # nothing to test: no draft yet
    loop.run_until_complete(store.set_draft(agent, {"schema": 2}, "tester"))
    r = c.post("/api/outbound-calls", json=body)
    assert r.status_code == 200, r.text
    got = c.get(f"/api/batch-calls/{r.json()['batch_call_id']}").json()
    assert got["name"] == "Test call to +966548802968" and got["config"]["draft"] is True
    plain = c.post("/api/outbound-calls", json={**body, "draft": False}).json()["batch_call_id"]
    assert "draft" not in c.get(f"/api/batch-calls/{plain}").json()["config"]              # a normal call stays on the published version
