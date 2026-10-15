"""An agent's test cases and the publish gate (Phase 12.7).

    GET    /api/agents/{id}/evals                  cases, the draft's gate status, the latest / running job
    PUT    /api/agents/{id}/evals/{case}           create / edit a case            {spec, gate}
    DELETE /api/agents/{id}/evals/{case}
    POST   /api/agents/{id}/evals/run              run cases on the draft          {cases?, only?: gate|failed|all}
    GET    /api/agents/{id}/audit                  publishes, rollbacks, gate overrides, case changes

Runs go through the same eval runner as `python -m evals` (fixture hospital backend, simulated caller), on the agent's
draft with its own models. Results of the gate cases are kept against the draft (see runtime/platform/gate.py);
Publish (agents_api) refuses while a gate case is failing or has not run, unless given a reason.
"""

import asyncio
import time
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from runtime.control.agents_api import _agent, _platform
from runtime.control.api import auth
from runtime.platform import gate

router = APIRouter(prefix="/api")


def _job_key(agent_id: str) -> str:
    return f"eval_job:{agent_id}"


@router.get("/agents/{agent_id}/evals", dependencies=auth)
async def list_cases(agent_id: str) -> dict:
    rt, store = _platform()
    a = await _agent(store, agent_id)
    cases = await store.eval_cases(agent_id)
    job = await store.meta_get(_job_key(agent_id))
    from runtime.control.eval_jobs import JOBS
    return {"cases": cases, "gate": gate.status(a, cases), "has_draft": a.get("draft") is not None,
            "job": {"id": job, **(await JOBS.get(job) or {})} if job else None}


class CaseBody(BaseModel):
    spec: dict[str, Any]
    gate: bool = True
    author: str = "console"


@router.put("/agents/{agent_id}/evals/{case_id}", dependencies=auth)
async def put_case(agent_id: str, case_id: str, body: CaseBody) -> dict:
    rt, store = _platform()
    await _agent(store, agent_id)
    if errors := gate.validate_case(case_id, body.spec):
        raise HTTPException(422, {"errors": errors})
    old = next((c for c in await store.eval_cases(agent_id) if c["id"] == case_id), None)
    await store.put_eval_case(agent_id, case_id, body.spec, body.gate, body.author)
    if old is None or old["spec"] != body.spec or old["gate"] != body.gate:
        await store.audit(agent_id, "case.created" if old is None else "case.changed", body.author,
                          {"case": case_id, "gate": body.gate,
                           **({"gate_was": old["gate"]} if old and old["gate"] != body.gate else {})})
    return {"ok": True}


@router.delete("/agents/{agent_id}/evals/{case_id}", dependencies=auth)
async def delete_case(agent_id: str, case_id: str, author: str = "console") -> dict:
    rt, store = _platform()
    await _agent(store, agent_id)
    old = next((c for c in await store.eval_cases(agent_id) if c["id"] == case_id), None)
    if old is None:
        raise HTTPException(404, "case not found")
    await store.delete_eval_case(agent_id, case_id)
    await store.audit(agent_id, "case.deleted", author, {"case": case_id, "gate": old["gate"]})
    return {"deleted": case_id}


class RunBody(BaseModel):
    cases: list[str] | None = None
    only: str = "gate"          # gate | failed (gate cases not passing yet) | all
    mode: str = "auto"


@router.post("/agents/{agent_id}/evals/run", dependencies=auth)
async def run_cases(agent_id: str, body: RunBody) -> dict:
    from evals.runner import case_from_spec, run_suite, save_report, store_run
    from runtime.control.eval_jobs import JOBS, spawn
    from runtime.platform.sync import WORKER_ID
    rt, store = _platform()
    a = await _agent(store, agent_id)
    rows = await store.eval_cases(agent_id)
    st = gate.status(a, rows)
    if body.cases:
        picked = [c for c in rows if c["id"] in body.cases]
    elif body.only == "failed":
        picked = [c for c in rows if c["gate"] and c["id"] not in st["passed"]]
    elif body.only == "all":
        picked = rows
    else:
        picked = [c for c in rows if c["gate"]]
    if not picked:
        raise HTTPException(400, "no test cases to run" + (" — every gate case already passed" if body.only == "failed"
                                                           and rows else ""))
    if await JOBS.running():
        raise HTTPException(409, "a test run is already in progress")
    on_draft = a.get("draft") is not None
    run_hash = gate.draft_hash(a)
    loaded = await (rt.loader.load_draft(agent_id) if on_draft else rt.loader.for_call(agent_id=agent_id))
    cases = [case_from_spec(c["id"], c["spec"]) for c in picked]
    specs = {c["id"]: c["spec"] for c in picked}
    job = f"job-{agent_id}-{int(time.time())}"
    await JOBS.create(job, len(cases), WORKER_ID)
    await store.meta_set(_job_key(agent_id), job)
    pending: list[asyncio.Task] = []

    def progress(r: dict) -> None:
        failed = gate.failed_lines(r)
        pending.append(asyncio.create_task(JOBS.progress(job, {"case": r["case"], "passed": r["passed"],
                                                               "failed": failed, "turns": r["turns"]})))

    async def work() -> None:
        try:
            run = await run_suite(cases, mode=body.mode, agent_llm=loaded.llm, progress=progress,
                                  bundle=loaded.inline_bundle())
            run["agent_id"] = agent_id
            run["target"] = {"draft": on_draft, "draft_hash": run_hash, "release": loaded.version}
            save_report(run)
            try:
                await store_run(run)
            except Exception:
                pass                                   # no eval_runs table (memory store): the report file is enough
            if on_draft:
                fresh = await store.agent(agent_id)     # results only count for the draft they ran on
                if fresh and gate.draft_hash(fresh) == run_hash:
                    await store.set_gate(agent_id, gate.merge_results(fresh, run_hash, specs, run))
            await asyncio.gather(*pending, return_exceptions=True)
            await JOBS.finish(job, run_id=run["id"])
        except Exception as e:
            await asyncio.gather(*pending, return_exceptions=True)
            await JOBS.finish(job, error=repr(e)[:300])

    spawn(work())
    return {"job": job, "total": len(cases), "draft": on_draft}


@router.get("/agents/{agent_id}/audit", dependencies=auth)
async def audit_log(agent_id: str, limit: int = 100) -> list[dict]:
    rt, store = _platform()
    await _agent(store, agent_id)
    return await store.audit_log(agent_id, limit)
