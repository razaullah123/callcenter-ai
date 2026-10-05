"""Phase 12.7: an agent's test cases in the database, and the publish gate (checks must pass on the draft, or a
reason is recorded)."""

import asyncio
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.config import get_settings
from runtime.platform import gate
from runtime.platform.loader import AgentLoader
from runtime.platform.seed import seed
from runtime.platform.store import MemoryStore
from runtime.tools import MockMCP

from .test_studio import _Rt

CASE = {"title": "Says hello", "caller": {"language": "en", "script": ["hi"]}, "expect": {"say": ["hello"]}}


@pytest.fixture
def studio(monkeypatch):
    import evals.runner as runner
    from runtime.control import agent_evals_api, agents_api
    from runtime.control.eval_jobs import JOBS
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

    async def no_pool():
        return None
    monkeypatch.setattr(JOBS, "_pool", no_pool)
    outcome: dict[str, bool] = {}            # case id → passed (default: passed)
    ran: list[list[str]] = []

    async def fake_suite(cases, *, progress=None, bundle=None, **kw):
        assert bundle and bundle["skill_files"]          # runs on the agent's own (draft) bundle
        ran.append([c.id for c in cases])
        results = []
        for c in cases:
            ok = outcome.get(c.id, True)
            r = {"case": c.id, "passed": ok, "turns": 3, "duration_s": 1.0,
                 "checks": [{"check": "said:hello", "passed": ok, "detail": "" if ok else "never said hello"}]}
            results.append(r)
            if progress:
                progress(r)
        return {"id": f"run-{len(ran)}", "results": results, "summary": {}}
    monkeypatch.setattr(runner, "run_suite", fake_suite)
    monkeypatch.setattr(runner, "save_report", lambda run: None)

    async def no_store(run):
        raise RuntimeError("no database")
    monkeypatch.setattr(runner, "store_run", no_store)
    app = FastAPI()
    app.include_router(agents_api.router)
    app.include_router(agent_evals_api.router)
    with TestClient(app) as c:
        yield c, store, loop, outcome, ran
    loop.close()


def _run(c, agent: str, **body) -> dict:
    r = c.post(f"/api/agents/{agent}/evals/run", json=body)
    assert r.status_code == 200, r.text
    for _ in range(500):
        job = c.get(f"/api/agents/{agent}/evals").json()["job"]
        if job and job.get("status") != "running":
            assert job["status"] == "done", job
            return c.get(f"/api/agents/{agent}/evals").json()
        time.sleep(0.02)
    raise AssertionError("the run did not finish")


def _draft_edit(c, agent: str, text: str) -> None:
    skill = f"{agent.replace('-', '_')}_main"
    files = c.get(f"/api/agents/{agent}").json()["skills"][skill]["files"]
    r = c.put(f"/api/agents/{agent}/draft/skills/{skill}", json={"files": {**files, "SKILL.md": files["SKILL.md"] + text}})
    assert r.status_code == 200, r.text


def test_repo_cases_are_seeded_as_gate_cases_of_the_hmg_agent(studio):
    from evals.runner import load_cases
    c, store, loop, *_ = studio
    body = c.get("/api/agents/hmg-care/evals").json()
    assert {x["id"] for x in body["cases"]} == {x.id for x in load_cases()}
    assert all(x["gate"] for x in body["cases"])
    loop.run_until_complete(store.delete_eval_case("hmg-care", body["cases"][0]["id"]))
    loop.run_until_complete(seed(store, get_settings()))                     # only once: deletions stay deleted
    assert len(c.get("/api/agents/hmg-care/evals").json()["cases"]) == len(body["cases"]) - 1


def test_case_specs_are_validated(studio):
    c, *_ = studio
    c.post("/api/agents", json={"name": "Clinic FAQ"})
    bad = c.put("/api/agents/clinic-faq/evals/x1", json={"spec": {"caller": {"language": "fr"}, "expect": {"bookd": 1}}})
    assert bad.status_code == 422
    errors = " ".join(bad.json()["detail"]["errors"])
    assert "caller.language" in errors and "script" in errors and "bookd" in errors
    assert c.put("/api/agents/clinic-faq/evals/Bad Id", json={"spec": CASE}).status_code == 422
    assert c.put("/api/agents/clinic-faq/evals/says_hello", json={"spec": CASE}).status_code == 200


def test_publish_needs_passing_checks_or_a_reason(studio):
    c, store, loop, outcome, ran = studio
    c.post("/api/agents", json={"name": "Clinic FAQ"})
    c.put("/api/agents/clinic-faq/evals/says_hello", json={"spec": CASE})
    c.put("/api/agents/clinic-faq/evals/says_bye", json={"spec": {**CASE, "title": "Bye"}})
    c.put("/api/agents/clinic-faq/evals/informational", json={"spec": {**CASE, "title": "Info"}, "gate": False})
    _draft_edit(c, "clinic-faq", "\nSay hello first.")
    r = c.post("/api/agents/clinic-faq/publish", json={"note": "hello"})
    assert r.status_code == 409 and set(r.json()["detail"]["gate"]["not_run"]) == {"says_hello", "says_bye"}

    outcome.update(says_bye=False, informational=False)
    body = _run(c, "clinic-faq", only="all")
    st = body["gate"]
    assert st["passed"] == ["says_hello"] and st["failed"] == ["says_bye"] and not st["ok"]
    assert "never said hello" in st["results"]["says_bye"]["failed"][0]
    assert c.post("/api/agents/clinic-faq/publish", json={}).status_code == 409
    assert c.post("/api/agents/clinic-faq/publish", json={"override_reason": "urgent"}).status_code == 422   # too short

    # re-run only what failed; now everything gating passes (the non-gate case may still fail)
    outcome["says_bye"] = True
    body = _run(c, "clinic-faq", only="failed")
    assert ran[-1] == ["says_bye"] and body["gate"]["ok"]
    r = c.post("/api/agents/clinic-faq/publish", json={"note": "hello first"})
    assert r.status_code == 200, r.text
    assert r.json()["gate"]["checks"] == {"required": 2, "passed": 2, "failed": [], "not_run": []}
    assert "override" not in r.json()["gate"]
    rel = c.get("/api/agents/clinic-faq").json()["releases"][0]
    assert rel["version"] == 2 and rel["gate"]["checks"]["passed"] == 2

    # next draft: old results are stale; publishing anyway needs a reason, recorded on the release and audited
    _draft_edit(c, "clinic-faq", "\nBe brief.")
    assert set(c.get("/api/agents/clinic-faq/evals").json()["gate"]["not_run"]) == {"says_hello", "says_bye"}
    reason = "hotfix for a wrong opening hour, checks run after"
    r = c.post("/api/agents/clinic-faq/publish", json={"override_reason": reason, "author": "raza"})
    assert r.status_code == 200 and r.json()["gate"]["override"] == {"reason": reason, "by": "raza"}
    audit = c.get("/api/agents/clinic-faq/audit").json()
    assert [a["action"] for a in audit[:2]] == ["publish.override", "publish"]
    assert audit[0]["detail"]["override"]["reason"] == reason and audit[0]["actor"] == "raza"
    c.post(f"/api/agents/clinic-faq/activate/{rel['id']}", params={"author": "raza"})
    assert c.get("/api/agents/clinic-faq/audit").json()[0]["action"] == "activate"


def test_a_changed_case_must_run_again(studio):
    c, *_ = studio
    c.post("/api/agents", json={"name": "Clinic FAQ"})
    c.put("/api/agents/clinic-faq/evals/says_hello", json={"spec": CASE})
    _draft_edit(c, "clinic-faq", "\nSay hello.")
    assert _run(c, "clinic-faq")["gate"]["ok"]
    c.put("/api/agents/clinic-faq/evals/says_hello", json={"spec": {**CASE, "expect": {"say": ["hello", "welcome"]}}})
    st = c.get("/api/agents/clinic-faq/evals").json()["gate"]
    assert st["not_run"] == ["says_hello"] and not st["ok"]
    actions = [a["action"] for a in c.get("/api/agents/clinic-faq/audit").json()]
    assert actions[:2] == ["case.changed", "case.created"]


def test_agent_without_gate_cases_publishes_as_before(studio):
    c, *_ = studio
    c.post("/api/agents", json={"name": "Clinic FAQ"})
    _draft_edit(c, "clinic-faq", "\nSay hello.")
    r = c.post("/api/agents/clinic-faq/publish", json={})
    assert r.status_code == 200 and r.json()["gate"]["checks"]["required"] == 0


def test_status_is_pure():
    agent = {"draft": {"a": 1}, "gate": None}
    cases = [{"id": "x", "spec": CASE, "gate": True}]
    assert gate.status(agent, cases)["not_run"] == ["x"]
    rec = gate.merge_results(agent, gate.draft_hash(agent), {"x": CASE},
                             {"id": "r1", "results": [{"case": "x", "passed": True, "checks": []}]})
    assert gate.status({**agent, "gate": rec}, cases)["ok"]
    assert not gate.status({"draft": None, "gate": rec}, cases)["ok"]          # nothing to publish
