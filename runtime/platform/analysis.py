"""Post-call analysis (Hamsa parity: Outcome + Satisfaction): after a call ends, the agent's own model reads the (already
PII-masked) transcript once and returns

    summary      2-3 sentences, in the language of the call
    sentiment    positive | neutral | negative  — the caller's tone by the end
    csat         1-5   how satisfied the caller probably was          (estimate)
    nps          0-10  how likely they would recommend the service     (estimate)
    resolved     true | false — the request was dealt with without a person taking over
    outcome      the agent's own fields (`analysis.fields`: name, type, description), filled from the conversation

Hamsa's CSAT / NPS come from per-call surveys; we have none, so ours are *estimates read from the transcript* and are labelled
so everywhere. The model never has the last word: every value is validated and dropped when it doesn't fit.

An agent's release may carry `analysis: {enabled, summary, sentiment, satisfaction, fields: [{name, type, description, options?}]}`.
`AnalysisSink` (an EventBus subscriber) runs it for every call that ends on an agent with it enabled — calls with fewer than
`MIN_TURNS` caller turns are recorded as skipped. The result is stored in `call_analysis`, shown in Call History → Outcome and the
dashboard's Satisfaction tab, and added to the `call.ended` webhook (`outcomeResult`, `analysis`). It can be run again on any
call from the console.
"""

import asyncio
import json
import logging
import re
import time
from typing import Any

from runtime.events import Event, EventType
from runtime.providers import TextDelta

log = logging.getLogger(__name__)

MIN_TURNS = 2                    # caller turns needed for an analysis to mean anything
TIMEOUT_S = 40.0
MAX_TRANSCRIPT_CHARS = 12_000
MAX_FIELDS = 20
FIELD_TYPES = ("string", "number", "boolean", "enum", "array", "object")
SENTIMENTS = ("positive", "neutral", "negative")
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,49}$")
KEEP_RESULT_S = 120.0            # how long a finished result stays available to the webhook


def analysis_errors(cfg: Any) -> list[str]:
    """Problems with a bundle's `analysis` section (absent / empty = off)."""
    if not cfg:
        return []
    if not isinstance(cfg, dict):
        return ["analysis must be an object"]
    errors = []
    for k in ("enabled", "summary", "sentiment", "satisfaction"):
        if k in cfg and not isinstance(cfg[k], bool):
            errors.append(f"analysis.{k} must be true or false")
    fields = cfg.get("fields") or []
    if not isinstance(fields, list) or len(fields) > MAX_FIELDS:
        return errors + [f"analysis.fields must be a list of at most {MAX_FIELDS} fields"]
    seen = set()
    for i, f in enumerate(fields, start=1):
        if not isinstance(f, dict):
            errors.append(f"analysis.fields[{i}] must be an object")
            continue
        name = f.get("name")
        if not isinstance(name, str) or not _NAME.match(name):
            errors.append(f"analysis.fields[{i}].name must be snake_case (lowercase letter first, 1-50 characters)")
        elif name in seen:
            errors.append(f"analysis.fields: {name} appears twice")
        seen.add(name)
        if f.get("type") not in FIELD_TYPES:
            errors.append(f"analysis.fields[{i}].type must be one of {', '.join(FIELD_TYPES)}")
        if f.get("type") == "enum" and not (isinstance(f.get("options"), list) and 2 <= len(f["options"]) <= 20
                                            and all(isinstance(o, str) and o for o in f["options"])):
            errors.append(f"analysis.fields[{i}].options: an enum needs 2-20 non-empty texts")
        if not isinstance(f.get("description", ""), str) or len(f.get("description", "")) > 300:
            errors.append(f"analysis.fields[{i}].description: at most 300 characters")
    return errors


_TYPE_OF = {"string": "string", "number": "number", "integer": "number", "boolean": "boolean", "array": "array", "object": "object"}


def fields_from_schema(shape: Any) -> list[dict]:
    """Hamsa's outcome schema (a JSON schema object: properties with type / description / enum) as our outcome fields. Names are
    made snake_case, unusable properties are skipped, at most MAX_FIELDS are kept."""
    props = shape.get("properties") if isinstance(shape, dict) else None
    if not isinstance(props, dict):
        return []
    out, seen = [], set()
    for raw, spec in props.items():
        if not isinstance(spec, dict):
            continue
        name = re.sub(r"[^a-z0-9]+", "_", re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", str(raw)).lower()).strip("_")
        if not name or not name[0].isalpha() or name in seen:
            continue
        seen.add(name)
        options = [str(o) for o in spec.get("enum") or [] if isinstance(o, (str, int, float)) and str(o)]
        field = {"name": name[:50], "type": "enum" if len(options) >= 2 and spec.get("type", "string") == "string" else _TYPE_OF.get(spec.get("type"), "string"),
                 "description": str(spec.get("description") or "")[:300]}
        if field["type"] == "enum":
            field["options"] = options[:20]
        out.append(field)
        if len(out) >= MAX_FIELDS:
            break
    return out


def settings_from_schema(shape: Any) -> dict | None:
    """The `analysis` section for an agent imported with an outcome schema: on, its fields, and the built-in summary / sentiment
    only where the schema doesn't already have a field of that name."""
    fields = fields_from_schema(shape)
    if not fields:
        return None
    names = {f["name"] for f in fields}
    return {"enabled": True, "summary": "summary" not in names, "sentiment": not any("sentiment" in n for n in names),
            "satisfaction": True, "fields": fields}


def enabled(cfg: dict | None) -> bool:
    return bool(cfg and cfg.get("enabled"))


# ---------------------------------------------------------------- the prompt and the validation

def build_prompt(cfg: dict, language: str | None) -> str:
    lang = {"ar": "Arabic", "en": "English"}.get(language or "", "the language of the call")
    keys, notes = [], []
    if cfg.get("summary", True):
        keys.append('"summary": "2-3 sentences: what the caller wanted and how the call ended, written in %s"' % lang)
    if cfg.get("sentiment", True):
        keys.append('"sentiment": "positive" | "neutral" | "negative"  (the caller\'s tone by the end of the call)')
    if cfg.get("satisfaction", True):
        keys.append('"csat": integer 1-5 (how satisfied the caller probably was) or null if you cannot tell')
        keys.append('"nps": integer 0-10 (how likely the caller would be to recommend this service) or null if you cannot tell')
        keys.append('"resolved": true | false  (the caller\'s request was handled without a person having to take over)')
    fields = cfg.get("fields") or []
    if fields:
        lines = []
        for f in fields:
            kind = {"enum": "one of " + " | ".join(f.get("options") or []), "array": "a JSON list",
                    "object": "a JSON object"}.get(f["type"], f["type"])
            lines.append(f'  - "{f["name"]}" ({kind}): {f.get("description") or f["name"]}')
        keys.append('"fields": an object with exactly these keys, each the value the call gave or null if it did not:\n' + "\n".join(lines))
        notes.append("Fill the fields only from what was actually said; never guess.")
    return ("You analyse one finished phone call between a voice AI agent (\"Agent\") and a caller (\"User\"). The transcript is "
            "Arabic (often Saudi) and/or English; personal details in it are masked. Judge only from what is written; the caller "
            "may be brief, and short or neutral calls are normal.\nReply with JSON only, no other text:\n{\n  "
            + ",\n  ".join(keys) + "\n}\n" + " ".join(notes))


def transcript_text(lines: list[dict]) -> str:
    out = [("Agent: " if t["role"] == "agent" else "User: ") + t["text"] for t in lines if t["role"] in ("agent", "user")]
    text = "\n".join(out)
    return text if len(text) <= MAX_TRANSCRIPT_CHARS else text[:MAX_TRANSCRIPT_CHARS // 2] + "\n…\n" + text[-MAX_TRANSCRIPT_CHARS // 2:]


def parse_json(text: str) -> dict:
    """The first JSON object in the model's reply (it may sit inside prose or a code fence)."""
    start = text.find("{")
    while start != -1:
        try:
            obj, _ = json.JSONDecoder().raw_decode(text[start:])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
        start = text.find("{", start + 1)
    return {}


def _int(v: Any, lo: int, hi: int) -> int | None:
    if isinstance(v, bool) or v is None:
        return None
    try:
        n = int(round(float(v)))
    except (TypeError, ValueError):
        return None
    return n if lo <= n <= hi else None


def _field(spec: dict, v: Any) -> Any:
    if v is None or v == "":
        return None
    kind = spec["type"]
    if kind == "string":
        return str(v).strip()[:500] or None
    if kind == "boolean":
        if isinstance(v, bool):
            return v
        return {"true": True, "yes": True, "false": False, "no": False}.get(str(v).strip().lower())
    if kind == "number":
        try:
            return float(v) if not isinstance(v, bool) else None
        except (TypeError, ValueError):
            return None
    if kind == "enum":
        return next((o for o in spec["options"] if str(v).strip().lower() == o.lower()), None)
    if kind == "array":
        if not isinstance(v, list):
            return None
        return [x if isinstance(x, (int, float, bool)) else str(x)[:200] for x in v[:50] if x is not None and not isinstance(x, (list, dict))] or None
    if kind == "object":
        if not isinstance(v, dict):
            return None
        out = {str(k)[:50]: (x if isinstance(x, (int, float, bool)) else str(x)[:200]) for k, x in list(v.items())[:20]
               if x is not None and not isinstance(x, (list, dict))}
        return out or None
    return None


def validate(obj: dict, cfg: dict) -> dict:
    """The model's answer as the values we keep: {summary, sentiment, csat, nps, resolved, outcome}."""
    out: dict[str, Any] = {"summary": None, "sentiment": None, "csat": None, "nps": None, "resolved": None, "outcome": {}}
    if cfg.get("summary", True) and isinstance(obj.get("summary"), str):
        out["summary"] = obj["summary"].strip()[:1000] or None
    if cfg.get("sentiment", True) and str(obj.get("sentiment", "")).lower() in SENTIMENTS:
        out["sentiment"] = str(obj["sentiment"]).lower()
    if cfg.get("satisfaction", True):
        out["csat"], out["nps"] = _int(obj.get("csat"), 1, 5), _int(obj.get("nps"), 0, 10)
        if isinstance(obj.get("resolved"), bool):
            out["resolved"] = obj["resolved"]
    given = obj.get("fields") if isinstance(obj.get("fields"), dict) else {}
    out["outcome"] = {f["name"]: _field(f, given.get(f["name"])) for f in cfg.get("fields") or []}
    return out


async def analyze(llm, cfg: dict, lines: list[dict], language: str | None) -> tuple[dict, str | None]:
    """(validated values, error). Never raises."""
    system = build_prompt(cfg, language)
    reply = ""
    try:
        async def run():
            nonlocal reply
            async for ev in llm.stream([{"role": "system", "content": system},
                                        {"role": "user", "content": "Transcript:\n" + transcript_text(lines)}],
                                       temperature=0, max_tokens=900):
                if isinstance(ev, TextDelta):
                    reply += ev.text
        await asyncio.wait_for(run(), TIMEOUT_S)
    except asyncio.TimeoutError:
        return {}, "the analysis model did not answer in time"
    except Exception as e:                                   # noqa: BLE001
        return {}, f"the analysis model failed ({e.__class__.__name__})"
    obj = parse_json(reply)
    if not obj:
        return {}, "the analysis model did not return JSON"
    return validate(obj, cfg), None


# ---------------------------------------------------------------- running it on a call

def user_turns(lines: list[dict]) -> int:
    return sum(1 for t in lines if t["role"] == "user")


async def run_for_call(rt, call_id: str, cfg: dict, *, release_id: int | None = None, force: bool = False) -> dict | None:
    """Analyse one ended call and store the result (a `call_analysis` row). None when there is nothing to analyse."""
    from runtime.control.store import get_call
    store = getattr(rt, "store", None)
    if store is not None:
        await store.flush()
    found = await get_call(call_id)
    if found is None:
        return None
    call, lines = found["call"], found["transcript"]
    row = {"call_id": call_id, "workspace_id": None, "agent_id": call.get("agent_id"), "started_at": call.get("started_at"),
           "status": "ok", "error": None, "summary": None, "sentiment": None, "csat": None, "nps": None, "resolved": None,
           "outcome": {}, "model": None}
    agent = await rt.platform.agent(call["agent_id"]) if call.get("agent_id") else None
    row["workspace_id"] = (agent or {}).get("workspace_id")
    if not row["workspace_id"]:
        return None
    if not force and user_turns(lines) < MIN_TURNS:
        row.update(status="skipped", error=f"the caller spoke fewer than {MIN_TURNS} times — nothing to analyse")
    else:
        rid = release_id or call.get("release_id")
        try:
            loaded = await rt.loader.load_release(rid) if rid else await rt.loader.for_call(agent_id=call.get("agent_id"))
            llm = loaded.llm
        except Exception as e:                               # noqa: BLE001
            llm = None
            row.update(status="failed", error=f"the agent's model could not be loaded ({e.__class__.__name__})")
        if llm is not None:
            t0 = time.monotonic()
            values, error = await analyze(llm, cfg, lines, call.get("language"))
            row.update(values)
            if error:
                row.update(status="failed", error=error)
            row["model"] = getattr(llm, "model", None) or llm.__class__.__name__
            log.info("call %s analysed in %.1fs (%s)", call_id, time.monotonic() - t0, row["status"])
    await rt.platform.put_call_analysis(row)
    return row


class AnalysisSink:
    """EventBus subscriber: analyses a call when it ends, if its release asks for it. `result()` lets the call webhook wait
    for the outcome (it is part of `call.ended`)."""

    def __init__(self, rt) -> None:
        self.rt = rt
        self._calls: dict[str, dict] = {}
        self._tasks: set[asyncio.Task] = set()

    async def __call__(self, e: Event) -> None:
        if not e.call_id or self.rt.platform is None:
            return
        if e.type == EventType.CALL_START:
            self._calls[e.call_id] = {"release_id": e.data.get("release_id"), "done": asyncio.get_running_loop().create_future()}
        elif e.type == EventType.CALL_END and (c := self._calls.get(e.call_id)) is not None and not c.get("started"):
            c["started"] = True
            t = asyncio.create_task(self._run(e.call_id, c), name=f"analysis-{e.call_id}")
            self._tasks.add(t)
            t.add_done_callback(self._tasks.discard)

    async def _run(self, call_id: str, c: dict) -> None:
        result = None
        try:
            release = await self.rt.platform.release(c["release_id"]) if c["release_id"] else None
            cfg = ((release or {}).get("bundle") or {}).get("analysis")
            if enabled(cfg):
                result = await run_for_call(self.rt, call_id, cfg, release_id=c["release_id"])
        except asyncio.CancelledError:
            raise
        except Exception:                                    # noqa: BLE001 — never affects anything else
            log.exception("call analysis failed for %s", call_id)
        finally:
            if not c["done"].done():
                c["done"].set_result(result)
            asyncio.get_running_loop().call_later(KEEP_RESULT_S, self._calls.pop, call_id, None)

    async def result(self, call_id: str, timeout: float = 30.0) -> dict | None:
        """The call's analysis once it is ready (None: not enabled, skipped, failed, unknown call or too slow)."""
        c = self._calls.get(call_id)
        if c is None:
            return None
        try:
            return await asyncio.wait_for(asyncio.shield(c["done"]), timeout)
        except asyncio.TimeoutError:
            return None

    async def close(self) -> None:
        for t in list(self._tasks):
            t.cancel()


def public(row: dict | None) -> dict | None:
    """What the API / webhook show of a stored analysis."""
    if not row:
        return None
    return {k: row.get(k) for k in ("status", "error", "summary", "sentiment", "csat", "nps", "resolved", "outcome", "model",
                                    "created_at")}


# ---------------------------------------------------------------- the dashboard numbers

def satisfaction_summary(rows: list[dict]) -> dict:
    """Hamsa's satisfaction numbers over analysed calls (estimates): CSAT = share of calls rated 4-5, NPS = promoters (9-10) minus
    detractors (0-6) in percent, the sentiment split, and how many calls each is based on."""
    ok = [r for r in rows if r.get("status") == "ok"]
    rated = [r["csat"] for r in ok if r.get("csat") is not None]
    scored = [r["nps"] for r in ok if r.get("nps") is not None]
    senti = [r["sentiment"] for r in ok if r.get("sentiment") in SENTIMENTS]

    def pct(n: int, d: int) -> float | None:
        return round(100 * n / d, 1) if d else None
    return {
        "analyzed": len(ok), "skipped": sum(1 for r in rows if r.get("status") == "skipped"),
        "failed": sum(1 for r in rows if r.get("status") == "failed"),
        "csat": {"score": pct(sum(1 for v in rated if v >= 4), len(rated)), "average": round(sum(rated) / len(rated), 2) if rated else None,
                 "calls": len(rated)},
        "nps": {"score": round(100 * (sum(1 for v in scored if v >= 9) - sum(1 for v in scored if v <= 6)) / len(scored)) if scored else None,
                "calls": len(scored)},
        "sentiment": {k: pct(sum(1 for s in senti if s == k), len(senti)) for k in SENTIMENTS} | {"calls": len(senti)},
        "resolved": {"score": pct(sum(1 for r in ok if r.get("resolved") is True), sum(1 for r in ok if r.get("resolved") is not None)),
                     "calls": sum(1 for r in ok if r.get("resolved") is not None)},
    }
