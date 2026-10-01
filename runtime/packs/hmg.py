"""HMG hospital pack: hooks for the Dr. Sulaiman Al Habib hospital system (HIS) tools and the booking flow.

Referenced by name from the tool policies (config/tools.yaml → the agent's release) and from
skills/book_appointment/SKILL.md (`turn_hooks: [hmg.booking]`). Nothing in the harness calls these directly.

Tool hooks
  hmg.offer_projects      post   hospitals the caller was offered (by name / nearby) — the only ones they may pick
  hmg.pick_offered_project pre   the clinic list is only fetched for an offered hospital
  hmg.clinic_list         post   the chosen hospital's clinics + its names from the reference table
  hmg.slot_search_gate    pre    the date question asked + answered; a requested day uses the by-date search;
                                 clinic from the list
  hmg.offered_slots       post   doctor → day → times offered in this call
  hmg.slot_offered        pre    only an offered doctor / day / time can be booked
  hmg.booked              post   booking bookkeeping
  hmg.prefetch_clinics    after  start fetching clinic lists as soon as hospitals are on the table (~2 s each)
Turn hook
  hmg.booking             dates / "earliest" / chosen time / "I'm at …" hints; the date question; slot prefetch
"""

from datetime import date
from typing import Any

from runtime.harness.nlu.dates import describe, resolve_date
from runtime.harness.nlu.extract import dateish
from runtime.harness.nlu.intents import describes_location, loose_earliest, mentions_since, wants_earliest
from runtime.text.arabic import normalize
from runtime.tools.hooks import HookCall, tool_hook, turn_hook
from runtime.tools.types import ToolError

CLINICS = "mssql_get_clinics_for_project"
NEAREST_SLOTS = "mssql_get_TopFive_nearestClinic_have_doctorSlots"
DATE_SLOTS = "mssql_get_TopFive_availableDoctors_with_slots_byDate"
FIND_BY_NAME, NEARBY = "find_hospital_by_name", "resolve_location"


# ---------------------------------------------------------------- hospitals

def _offered_ids(data: Any) -> list[int]:
    d = data if isinstance(data, dict) else {}
    if "status" in d:                                   # find_hospital_by_name: match / ambiguous / none
        found = [d["hospital"]] if d.get("status") == "match" else d.get("candidates", [])
        return [int(h["project_id"]) for h in found]
    return [int(p["ProjectID"]) for p in d.get("projects", []) if p.get("ProjectID") is not None]


@tool_hook("hmg.offer_projects", "post")
def offer_projects(h: HookCall) -> None:
    h.session.memory.setdefault("offered_projects", set()).update(_offered_ids(h.result.data))


@tool_hook("hmg.pick_offered_project", "pre")
def pick_offered_project(h: HookCall) -> None:
    if int(h.args.get("projectId", -1)) not in h.session.memory.get("offered_projects", set()):
        raise ToolError(f"ask the caller which hospital they want first (by name with {FIND_BY_NAME}, or by "
                        f"location with {NEARBY}); never choose a hospital for them")


@tool_hook("hmg.clinic_list", "post")
def clinic_list(h: HookCall) -> None:
    s, result = h.session, h.result
    if result.ok and isinstance(result.data, list):
        s.slots["project_id"] = h.args.get("projectId")
        # the hospital's names from the reference table — also when it was picked from the nearby list
        from runtime.tools.summarizers import _reference_names
        names = _reference_names().get(int(h.args.get("projectId") or -1))
        if names:
            s.slots["project_name"], s.slots["project_name_en"] = names.get("ar"), names.get("en")
        s.memory["clinics"] = {int(c["ClinicID"]): str(c.get("ClinicDescriptionN", "")).strip()
                               for c in result.data if isinstance(c, dict) and "ClinicID" in c}


@tool_hook("hmg.prefetch_clinics", "after")
def prefetch_clinics(h: HookCall) -> None:
    if not h.result.ok or h.agent is None:
        return
    d = h.result.data if isinstance(h.result.data, dict) else {}
    ids = ([d["hospital"]["project_id"]] if d.get("status") == "match" else
           [c["project_id"] for c in d.get("candidates", [])[:2]] if d.get("status") == "ambiguous" else
           [p["ProjectID"] for p in (d.get("projects") or [])[:3] if p.get("ProjectID") is not None])
    for pid in ids:
        h.agent.executor.prefetch(CLINICS, {"projectId": int(pid)}, h.agent._ctx(), h.agent.ev)


# ---------------------------------------------------------------- slots

@tool_hook("hmg.slot_search_gate", "pre")
def slot_search_gate(h: HookCall) -> None:
    m, s = h.session.memory, h.session
    nearest = "date" not in (h.tool.input_schema.get("properties") or {})     # the earliest-slots search
    # The date question must have been asked and answered — but the *model* reads the answer. (A regex on the
    # transcript blocked a correct "earliest" call when STT misheard "أقرب موعد متاح" as "موعد مطاح" and the agent
    # looped on the question.)
    asked_at = m.get("date_question_turn")
    answered = asked_at is not None and s.turn_id > asked_at
    if nearest and not m.get("requested_date") and not m.get("wants_earliest") and not answered:
        raise ToolError("first ask the caller exactly: \"Would you like the earliest available appointment, or do "
                        "you have a specific date in mind?\" (Arabic: \"تبي أقرب موعد متاح، ولا عندك تاريخ معين؟\") "
                        "and wait for the answer")
    if nearest and (day := m.get("requested_date")):
        # The earliest-slots list only covers the first days with slots: it can't say a requested day is full.
        raise ToolError(f"the caller asked for {day}: call {DATE_SLOTS} with date {day} instead. Never say a day "
                        "has no appointments without searching that day.")
    if int(h.args.get("clinicId", -1)) not in (m.get("clinics") or {}):
        raise ToolError(f"use a clinicId from the clinic list of the chosen hospital (call {CLINICS} first)")


@tool_hook("hmg.offered_slots", "post")
def offered_slots(h: HookCall) -> None:
    from runtime.tools.summarizers import doctor_rows
    if not (h.result.ok and isinstance(h.result.data, dict)):
        return
    offered = h.session.memory.setdefault("offered_slots", {})
    for doc in doctor_rows(h.result.data):
        days = offered.setdefault(doc["doctor_id"], {})
        for day, times in doc["slots"].items():
            days.setdefault(day[:10], set()).update(times)


@tool_hook("hmg.slot_offered", "pre")
def slot_offered(h: HookCall) -> None:
    offered = h.session.memory.get("offered_slots") or {}
    times = offered.get(int(h.args.get("DoctorID", -1)), {}).get(str(h.args.get("StrAppointmentDate", ""))[:10])
    if not times or str(h.args.get("StartTime", "")) not in times:
        raise ToolError("that doctor / day / time was not offered; offer times from the latest slot search and "
                        "book only what the caller chose")


@tool_hook("hmg.booked", "post")
def booked(h: HookCall) -> None:
    if h.result.ok:
        h.session.slots.update({"booked": True, "appointment": h.result.data, "project_id": h.args.get("ProjectID")})


# ---------------------------------------------------------------- the booking conversation

DATE_QUESTION = ("أقرب موعد متاح", "earliest available appointment")


def _last_agent_line(session) -> str:
    return next((m.get("content") or "" for m in reversed(session.history) if m.get("role") == "assistant"), "")


def _asked_date_question(session) -> bool:
    last = _last_agent_line(session)
    return DATE_QUESTION[0] in last or DATE_QUESTION[1] in last.lower()


def _offered_times_last(session) -> bool:
    last = _last_agent_line(session)
    return bool(session.memory.get("offered_slots")) and ("time slots" in last.lower() or "الأوقات المتاحة" in last)


@turn_hook("hmg.booking")
class Booking:
    """While booking: which detail the caller's reply may hold, and what it says (dates, times, where they are)."""

    def awaits(self, agent, text: str) -> tuple[str, dict[str, Any]] | None:
        s = agent.s
        if not s.auth.verified:
            return None
        if _offered_times_last(s) and not s.pending_action:
            offered: set[str] = set()
            for days in (s.memory.get("offered_slots") or {}).values():
                for times in days.values():
                    offered |= set(times)
            return "time_choice", {"offered": offered}
        if not s.slots.get("project_id") or resolve_date(text) or wants_earliest(text) or mentions_since(text):
            return None
        if _asked_date_question(s) or dateish(text):
            return "date", {}
        return None

    def hints(self, agent, text: str, x: dict[str, Any]) -> list[str]:
        s, hints = agent.s, []
        if not s.auth.verified:
            return hints
        if x.get("time_choice"):
            hints.append(f"the caller chose the time {x['time_choice']}")
        elif x.get("date") == "earliest":
            self._earliest(s)
            hints.append("the caller wants the earliest available appointment")
        elif isinstance(x.get("date"), date):
            hints.append(self._day(s, x["date"]))
        elif not mentions_since(text) and (d := resolve_date(text)):
            hints.append(self._day(s, d))                  # slot searches must use this day (slot_search_gate)
        elif wants_earliest(text) or (_asked_date_question(s) and loose_earliest(text)):
            self._earliest(s)
            hints.append("the caller wants the earliest available appointment")
        if "project_id" not in s.slots and describes_location(text):
            hints.append(f"the caller said where they ARE — search nearby hospitals with {NEARBY}, not {FIND_BY_NAME}")
        return hints

    @staticmethod
    def _day(s, d: date) -> str:
        s.memory["requested_date"] = d.isoformat()
        s.memory.pop("wants_earliest", None)
        return f"date: {describe(d, s.language.language)}"

    @staticmethod
    def _earliest(s) -> None:
        s.memory.pop("requested_date", None)
        s.memory["wants_earliest"] = True

    def on_say(self, agent, text: str) -> None:
        if DATE_QUESTION[0] in text or DATE_QUESTION[1] in text.lower():
            agent.s.memory["date_question_turn"] = agent.s.turn_id    # the caller's next reply answers it

    def after_text(self, agent, text: str) -> None:
        """When the agent names a clinic (suggesting it), fetch that clinic's nearest slots."""
        s = agent.s
        clinics: dict[int, str] = s.memory.get("clinics") or {}
        project_id = s.slots.get("project_id")
        if not clinics or project_id is None:
            return
        said = normalize(text)
        mentioned = [cid for cid, name in clinics.items() if len(normalize(name)) >= 4 and normalize(name) in said]
        day = s.memory.get("requested_date")
        for cid in mentioned[:2]:
            args = {"projectId": int(project_id), "clinicId": cid}
            agent.executor.prefetch(DATE_SLOTS if day else NEAREST_SLOTS, {**args, "date": day} if day else args,
                                    agent._ctx(), agent.ev)
