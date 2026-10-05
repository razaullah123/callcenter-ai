"""Publish gate (Phase 12.7): an agent's test cases must pass on its draft before the draft is published.

Each case result is kept against the draft it ran on (`content_hash` of the draft bundle) and the case's spec, so a
change to either makes the result stale and the case has to run again. Results of several runs are merged — re-running
only the failed cases is enough. Publishing without a pass needs a reason, which is recorded on the release and in the
agent's audit log.
"""

import re
from datetime import datetime, timezone
from typing import Any

from .bundle import content_hash

EXPECT_KEYS = {"verified", "handoff", "tools_called", "tools_not_called", "slots", "booked", "booked_first_doctor",
               "booked_offered_doctor", "max_turns", "language", "gender", "no_medical_advice", "say", "never_say",
               "max_words_per_reply", "max_questions_per_reply"}
CASE_ID = re.compile(r"^[a-z0-9][a-z0-9_\-]{1,60}$")
MIN_OVERRIDE_REASON = 10


def spec_hash(spec: dict[str, Any]) -> str:
    return content_hash(spec)


def validate_case(case_id: str, spec: dict[str, Any]) -> list[str]:
    """What would stop the case from running (the runner's own format, evals/cases/*.yaml)."""
    errors = []
    if not CASE_ID.match(case_id):
        errors.append("id: lower-case letters, digits, _ or - (2-61 characters)")
    caller = spec.get("caller")
    if not isinstance(caller, dict):
        return errors + ["caller: required (language, and a script or a goal)"]
    if caller.get("language", "ar") not in ("ar", "en"):
        errors.append("caller.language: ar or en")
    script = caller.get("script")
    if script is not None and not (isinstance(script, list) and script and all(isinstance(x, str) for x in script)):
        errors.append("caller.script: a list of the caller's lines")
    if not script and not str(caller.get("goal") or "").strip():
        errors.append("caller: give a script (exact lines) or a goal (for the simulated caller)")
    expect = spec.get("expect") or {}
    if not isinstance(expect, dict):
        errors.append("expect: a mapping of checks")
    else:
        unknown = sorted(set(expect) - EXPECT_KEYS)
        if unknown:
            errors.append(f"expect: unknown checks {unknown} (known: {', '.join(sorted(EXPECT_KEYS))})")
    if not isinstance(spec.get("max_turns", 18), int) or not 1 <= spec.get("max_turns", 18) <= 40:
        errors.append("max_turns: 1-40")
    return errors


def draft_hash(agent: dict[str, Any]) -> str | None:
    return content_hash(agent["draft"]) if agent.get("draft") is not None else None


def status(agent: dict[str, Any], cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Where the draft stands: which gate cases passed on it, failed, or still have to run."""
    h = draft_hash(agent)
    rec = agent.get("gate") or {}
    current = (rec.get("cases") or {}) if h and rec.get("draft_hash") == h else {}
    results, passed, failed, not_run = {}, [], [], []
    for c in cases:
        r = current.get(c["id"])
        fresh = r is not None and r.get("spec_hash") == spec_hash(c["spec"])
        if fresh:
            results[c["id"]] = r
        if not c.get("gate"):
            continue
        (not_run if not fresh else passed if r["passed"] else failed).append(c["id"])
    required = len(passed) + len(failed) + len(not_run)
    return {"draft_hash": h, "required": required, "passed": passed, "failed": failed, "not_run": not_run,
            "ok": h is not None and not failed and not not_run, "results": results}


def merge_results(agent: dict[str, Any], run_hash: str, cases: dict[str, dict], run: dict[str, Any]) -> dict:
    """The agent's gate record after `run` (made on the draft with hash `run_hash`)."""
    rec = agent.get("gate") or {}
    if rec.get("draft_hash") != run_hash:
        rec = {"draft_hash": run_hash, "cases": {}}
    at = datetime.now(timezone.utc).isoformat()
    for r in run["results"]:
        spec = cases.get(r["case"])
        if spec is None:
            continue
        rec["cases"][r["case"]] = {
            "passed": bool(r["passed"]), "spec_hash": spec_hash(spec), "run_id": run["id"], "at": at,
            "turns": r.get("turns"), "duration_s": r.get("duration_s"),
            "failed": [f"{c['check']}{': ' + c['detail'] if c.get('detail') else ''}"[:200]
                       for c in r["checks"] if not c["passed"]]}
    return rec


def release_gate(st: dict[str, Any], *, override: str | None = None, by: str | None = None) -> dict[str, Any]:
    """What a release records about how it passed the gate."""
    out = {"checks": {"required": st["required"], "passed": len(st["passed"]), "failed": st["failed"],
                      "not_run": st["not_run"]},
           "runs": sorted({r["run_id"] for r in st["results"].values() if r.get("run_id")})}
    if override:
        out["override"] = {"reason": override, "by": by}
    return out
