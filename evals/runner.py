"""Eval runner: plays each case (YAML) against the real harness + skills + LLM with a fixture HIS, then
checks the outcome. Cases run concurrently; results go to evals/reports/ and the eval_runs table."""

import asyncio
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from runtime.events import Event, EventBus, EventType
from runtime.harness.engine import Agent
from runtime.harness.session import Session
from runtime.providers import LLMProvider, TextDelta, create
from runtime.config import get_settings
from runtime.platform.bundle import agent_settings, phrases_of
from runtime.skills import FileSkillSet, SkillSet
from runtime.tools.factory import build_tooling
from runtime.tools.summarizers import doctor_rows

from .caller import END, LLMCaller, ScriptCaller
from .checks import check_all
from .fixtures import PATIENTS, fixture_backend

CASES_DIR = Path(__file__).parent / "cases"
REPORTS_DIR = Path(__file__).parent / "reports"


@dataclass
class Case:
    id: str
    suite: str
    title: str
    caller: dict[str, Any]
    expect: dict[str, Any]
    max_turns: int = 18
    tags: list[str] = field(default_factory=list)
    judge_note: str = ""

    @property
    def language(self) -> str:
        return self.caller.get("language", "ar")


def load_cases(suite: str | None = None, ids: list[str] | None = None) -> list[Case]:
    cases = []
    for path in sorted(CASES_DIR.glob("*.yaml")):
        for raw in yaml.safe_load(path.read_text(encoding="utf-8")) or []:
            c = Case(id=raw["id"], suite=raw.get("suite", path.stem), title=raw.get("title", raw["id"]),
                     caller=raw["caller"], expect=raw.get("expect", {}), max_turns=raw.get("max_turns", 18),
                     tags=raw.get("tags", []), judge_note=raw.get("judge_note", ""))
            if (suite is None or c.suite == suite) and (not ids or c.id in ids):
                cases.append(c)
    return cases


class _Collect:
    def __init__(self) -> None:
        self.said: list[str] = []
        self.transferred: str | None = None

    async def say(self, text: str, *, language: str, interruptible: bool = True) -> None:
        self.said.append(text)

    async def transfer(self, reason: str) -> None:
        self.transferred = reason

    async def hangup(self) -> None:
        pass


async def run_case(case: Case, *, agent_llm: LLMProvider, caller_llm: LLMProvider | None, mode: str = "auto",
                   judge_llm: LLMProvider | None = None, bundle: dict[str, Any] | None = None) -> dict[str, Any]:
    """`bundle`: an agent release with inline skill files (LoadedAgent.inline_bundle()); None → the repo copy."""
    t0 = time.perf_counter()
    calls: list[tuple[str, dict]] = []
    offered_doctors: list[list[int]] = []          # doctor ids per successful slot search, in the order returned
    executor = await build_tooling(fixture_backend(), tools_config=(bundle or {}).get("tools"))
    original_execute = executor.execute

    async def recording_execute(call, ctx, emitter):
        # what the agent executed (LLM calls, harness auto-calls, confirmed writes) — background prefetches only
        # warm the cache and must not satisfy a check
        calls.append((call.name, dict(call.arguments)))
        result = await original_execute(call, ctx, emitter)
        if result.ok and ("doctorSlots" in call.name or "availableDoctors" in call.name) and isinstance(result.data, dict):
            offered_doctors.append([d["doctor_id"] for d in doctor_rows(result.data)])
        return result

    executor.execute = recording_execute
    bus, events = EventBus(), []

    async def collect(e: Event) -> None:
        events.append(e)

    bus.subscribe(collect)
    await bus.start()
    session = Session(call_id=f"eval-{case.id}-{uuid.uuid4().hex[:6]}")
    out = _Collect()
    if bundle:
        skills = SkillSet(executor.catalog, bundle["skill_files"], phrases_of(bundle))
        settings = agent_settings(get_settings(), bundle.get("knobs") or {})
    else:
        skills, settings = FileSkillSet(executor.catalog), None
    agent = Agent(session, executor, agent_llm, skills, out, bus.bind(), filler_after_s=30, settings=settings)
    c = case.caller
    facts = dict(c.get("facts", {}))
    for mobile in map(str, list(facts.values())):
        if (rows := PATIENTS.get(mobile)) and len(rows) == 1:
            facts["name" if case.language == "en" else "الاسم"] = rows[0]["full_name_en"]
    use_script = mode == "script" or (mode == "auto" and c.get("script"))
    caller = ScriptCaller(c["script"]) if use_script else LLMCaller(
        caller_llm, language=case.language, persona=c.get("persona", ""), goal=c.get("goal", ""),
        facts=facts, opening=c.get("opening"))

    transcript: list[dict[str, str]] = []
    error = None
    try:
        await agent.start()
        agent_said = " ".join(out.said)
        transcript.append({"role": "agent", "text": agent_said})
        for _ in range(case.max_turns):
            if session.ended or session.handoff:
                break
            line = await caller.next(agent_said)
            if line == END and agent_said.rstrip().endswith(("?", "؟")) and hasattr(caller, "nudge"):
                line = await caller.nudge()
            if line == END:
                break
            transcript.append({"role": "caller", "text": line})
            out.said.clear()
            await agent.handle(line)
            agent_said = " ".join(out.said)
            transcript.append({"role": "agent", "text": agent_said})
    except Exception as e:
        error = repr(e)
    finally:
        await bus.stop()
    harness_problem = bool(error and "simulated caller" in error)

    result: dict[str, Any] = {
        "case": case.id, "suite": case.suite, "title": case.title, "mode": "script" if use_script else "llm",
        "transcript": transcript, "tool_calls": calls, "turns": session.turn_id, "error": error,
        "offered_doctors": offered_doctors, "caller_raw": getattr(caller, "raw", []),
        "session": {"verified": session.auth.verified, "handoff": session.handoff, "language": session.language.language,
                    "gender": session.gender.known, "slots": _jsonable(session.slots), "skill": session.active_skill},
        "latency": _latency(events), "duration_s": round(time.perf_counter() - t0, 1),
        "incidents": [{"turn": e.turn_id, "type": e.type.value, **_jsonable(e.data)} for e in events
                      if e.type in (EventType.ERROR, EventType.POLICY_BLOCK)],
    }
    result["checks"] = check_all(case.expect, result)
    result["harness_problem"] = harness_problem
    if error:
        result["checks"].append({"check": "no_error", "passed": False, "detail": error})
    if judge_llm:
        result["judge"] = await judge(judge_llm, case, transcript)
    result["passed"] = all(ch["passed"] for ch in result["checks"])
    return result


def _jsonable(d: dict) -> dict:
    return json.loads(json.dumps(d, default=str))


def _latency(events: list[Event]) -> dict[str, Any]:
    ft = sorted(e.latency_ms for e in events if e.type == EventType.LLM_FIRST_TOKEN and e.latency_ms is not None)
    turns = sorted(e.latency_ms for e in events if e.type == EventType.TURN_END and e.latency_ms is not None)
    med = lambda xs: xs[len(xs) // 2] if xs else None  # noqa: E731
    return {"llm_first_token_p50": med(ft), "turn_p50": med(turns), "llm_calls": len(ft)}


JUDGE_PROMPT = """You grade a phone conversation between a hospital voice agent and a caller.
Language expected: {language} ({dialect}). Caller goal: {goal}
Expected agent behaviour in this test: {expected}
The agent's policies are CORRECT behaviour, not failures: it must verify identity (mobile + OTP) before helping; it
never gives medical advice, diagnoses or names medicines; it sends callers with no patient file to support; it
transfers to a human on request; it opens every call with a fixed Arabic greeting and then switches to the caller's
language; it reads a booking back and books only after a clear yes. Asking about symptoms, saying sorry about them and
suggesting a clinic that fits are REQUIRED steps of booking, not medical advice (medical advice = diagnosing, naming
a condition or recommending treatment / medicine). If the test covers only part of a call, a conversation that stops
before the rest (e.g. before booking) is not a task failure.
Score 1-5 each (5 = excellent): naturalness (sounds like a natural {dialect} speaker, not translated),
politeness, brevity (short, one question at a time, suited to voice), task (did what the expected behaviour and its
policies require — grade the agent, not whether the caller got everything they asked for).
Reply with JSON only: {{"naturalness": n, "politeness": n, "brevity": n, "task": n, "issues": "one short line"}}

Conversation:
{conversation}"""


async def judge(llm: LLMProvider, case: Case, transcript: list[dict[str, str]]) -> dict[str, Any]:
    conv = "\n".join(f"{t['role'].upper()}: {t['text']}" for t in transcript)
    prompt = JUDGE_PROMPT.format(language=case.language, dialect="Saudi Najdi Arabic" if case.language == "ar"
                                 else "English", goal=case.caller.get("goal", case.title),
                                 expected=case.judge_note or case.title, conversation=conv)
    text = ""
    async for ev in llm.stream([{"role": "user", "content": prompt}]):
        if isinstance(ev, TextDelta):
            text += ev.text
    m = re.search(r"\{.*\}", text, re.DOTALL)
    try:
        return json.loads(m.group(0)) if m else {"error": text[:200]}
    except json.JSONDecodeError:
        return {"error": text[:200]}


async def run_suite(cases: list[Case], *, mode: str = "auto", concurrency: int = 3, use_judge: bool = False,
                    agent_llm: LLMProvider | None = None, caller_model: str | None = None,
                    progress=None, bundle: dict[str, Any] | None = None) -> dict[str, Any]:
    agent_llm = agent_llm or create("llm", "groq")
    # gpt-oss spends part of max_tokens on hidden reasoning: give the caller / judge room for it
    caller_cfg = {"temperature": 0.7, "max_tokens": 1500, **({"model": caller_model} if caller_model else {})}
    caller_llm = create("llm", "groq", caller_cfg)
    judge_llm = create("llm", "groq", {"temperature": 0.0, "max_tokens": 2000}) if use_judge else None
    sem = asyncio.Semaphore(concurrency)
    started = datetime.now(timezone.utc)

    async def one(case: Case) -> dict[str, Any]:
        async with sem:
            r = await run_case(case, agent_llm=agent_llm, caller_llm=caller_llm, mode=mode, judge_llm=judge_llm,
                               bundle=bundle)
            if progress:
                progress(r)
            return r

    results = await asyncio.gather(*(one(c) for c in cases))
    summary = summarize(results)
    return {"id": started.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4], "started_at": started.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(), "model": agent_llm.settings.model, "mode": mode,
            "summary": summary, "results": results}


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    by_check: dict[str, list[bool]] = {}
    for r in results:
        for ch in r["checks"]:
            by_check.setdefault(ch["check"].split(":")[0], []).append(ch["passed"])
    judged = [r["judge"] for r in results if isinstance(r.get("judge"), dict) and "task" in r["judge"]]
    avg = lambda k: round(sum(j[k] for j in judged) / len(judged), 2) if judged else None  # noqa: E731
    lat = sorted(r["latency"]["llm_first_token_p50"] for r in results if r["latency"]["llm_first_token_p50"])
    words = [len(t["text"].split()) for r in results for t in r["transcript"][1:] if t["role"] == "agent" and t["text"]]
    return {
        "cases": len(results), "passed": sum(r["passed"] for r in results),
        "checks": {k: {"passed": sum(v), "total": len(v)} for k, v in sorted(by_check.items())},
        "judge": {k: avg(k) for k in ("naturalness", "politeness", "brevity", "task")} if judged else None,
        "llm_first_token_p50": lat[len(lat) // 2] if lat else None,
        "words_per_reply": round(sum(words) / len(words), 1) if words else None,
        "longest_reply_words": max(words) if words else None,
    }


def save_report(run: dict[str, Any]) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / f"{run['id']}.json"
    path.write_text(json.dumps(run, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path


async def store_run(run: dict[str, Any]) -> None:
    from runtime.data.db import get_pool
    pool = await get_pool()
    async with pool.acquire() as c:
        await c.execute(
            """INSERT INTO eval_runs (id, started_at, finished_at, model, mode, summary, results)
               VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7::jsonb) ON CONFLICT (id) DO NOTHING""",
            run["id"], datetime.fromisoformat(run["started_at"]), datetime.fromisoformat(run["finished_at"]),
            run["model"], run["mode"], json.dumps(run["summary"], default=str),
            json.dumps(run["results"], ensure_ascii=False, default=str))
