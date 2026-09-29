"""Tool-specific result summarizers (shapes observed from the live HIS, see scripts/probe_mcp.py)."""

import re
from datetime import date, timedelta
from typing import Any

from .summarize import summarizer

# Clinics that can't be booked by a patient over the phone.
NON_BOOKABLE_CLINIC = re.compile(
    r"livecare|blood bank|طوارئ|طلب بنفسه|العناية المركزة|فريق الاستجابة|مركز التدريب|الامتياز|الحاج$",
    re.IGNORECASE)

# Schedule entries that are services / packages, not doctors.
NOT_A_DOCTOR = re.compile(r"package|service|technician|nursing|booking|^\W*$", re.IGNORECASE)

MAX_TIMES_PER_DATE = 12
MAX_NEARBY_HOSPITALS = 3


def _spoken_case(name: str) -> str:
    """"DERMATOLOGY AND COSMETOLOGY" → "Dermatology and Cosmetology" (ENT / OB-GYNE stay as they are)."""
    if not name.isascii() or not name.isupper():
        return name
    small = {"and", "of", "the", "for", "&"}

    def word(w: str) -> str:
        if w.lower() in small:
            return w.lower()
        return w if "-" in w or len(w) <= 3 else w.capitalize()     # ENT, OB-GYNE: acronyms stay
    return " ".join(word(w) for w in name.split())


def _clean(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().rstrip(".").strip()


@summarizer("mssql_get_Projects_from_Location")
def nearby_projects(data: Any) -> Any:
    if not isinstance(data, dict) or "projects" not in data:
        return data
    names = _reference_names()
    out = []
    for p in data["projects"]:
        pid = p.get("ProjectID")
        ref = names.get(pid, {})
        out.append({"project_id": pid, "name_ar": ref.get("ar") or _clean(p.get("ProjectNameN")) or None,
                    "name_en": ref.get("en") or _clean(p.get("ProjectName")) or None,
                    "distance_km": p.get("distance_km")})
    out.sort(key=lambda h: h["distance_km"] if isinstance(h["distance_km"], (int, float)) else float("inf"))
    return {"hospitals": [{k: v for k, v in h.items() if v is not None} for h in out[:MAX_NEARBY_HOSPITALS]]}


def _reference_names() -> dict[int, dict[str, str]]:
    """project_id → {"ar", "en"} from the in-memory projects table (if loaded)."""
    from runtime.data import reference
    from runtime.tools.local_tools.hospitals import spoken_arabic_name
    ref = reference._ref
    if ref is None:
        return {}
    return {p.reference_id: {"en": p.project_name, "ar": spoken_arabic_name(p)} for p in ref.projects}


@summarizer("mssql_get_clinics_for_project")
def clinics(data: Any) -> Any:
    if not isinstance(data, list):
        return data
    rows = [(c.get("ClinicID"), _spoken_case(_clean(c.get("ClinicDescriptionN") or c.get("ClinicDescription"))))
            for c in data if isinstance(c, dict)]
    bookable = [f"{cid}:{name}" for cid, name in rows if cid is not None and not NON_BOOKABLE_CLINIC.search(name)]
    return "clinics (id:name): " + " | ".join(bookable)


def is_doctor(entry: dict) -> bool:
    return not (NOT_A_DOCTOR.search(entry.get("doctorName") or "") or
                NOT_A_DOCTOR.search(entry.get("doctorNameN") or ""))


def doctor_rows(data: dict) -> list[dict]:
    """Doctors with their slots, pseudo-doctors removed. Used by the summarizer and the harness."""
    out = []
    for entry in data.get("doctors") or data.get("clinics") or []:
        if not isinstance(entry, dict) or not is_doctor(entry):
            continue
        dates = {k: [s["startTime"] for s in v if isinstance(s, dict) and "startTime" in s]
                 for k, v in entry.items() if isinstance(v, list)}
        dates = {d: times for d, times in sorted(dates.items()) if times}
        if not dates:
            continue
        name_en = short_name(_clean(entry.get("doctorName")).title())
        name_ar = short_name(_clean(entry.get("doctorNameN")))
        out.append({"doctor_id": int(entry["doctorId"]), "name_ar": name_ar if name_ar != name_en else None,
                    "name_en": name_en, "slots": dates})
    return out


_WEEKDAYS_AR = ["الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]
_WEEKDAYS_EN = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def label_date(iso: str, today: date | None = None) -> str:
    """'2026-09-27' → '2026-09-27 الأحد/Sunday (today)' so the LLM never guesses the weekday."""
    try:
        d = date.fromisoformat(iso[:10])
    except ValueError:
        return iso
    if today is None:
        from runtime.harness.nlu.dates import today_riyadh
        today = today_riyadh()
    rel = {today: " (today)", today + timedelta(days=1): " (tomorrow)"}.get(d, "")
    return f"{d.isoformat()} {_WEEKDAYS_AR[d.weekday()]}/{_WEEKDAYS_EN[d.weekday()]}{rel}"


def label_time(hhmm: str) -> str:
    """'14:30' → '14:30 (2:30 PM / 2:30 مساءً)': the model books with the 24-h value and copies the 12-h one for the
    call language without converting (it got 14:30 → "four thirty" wrong). The TTS step turns it into words."""
    try:
        h, m = (int(x) for x in str(hhmm).split(":")[:2])
    except ValueError:
        return hhmm
    spoken = f"{(h % 12) or 12}" + (f":{m:02d}" if m else "")
    return f"{hhmm} ({spoken} {'AM' if h < 12 else 'PM'} / {spoken} {'صباحاً' if h < 12 else 'مساءً'})"


_NAME_PARTICLES = {"al", "el", "bin", "ibn", "abu", "abo", "bint", "بن", "بنت", "ابو", "أبو", "ال"}
_NAME_PREFIXES = {"abd", "abdul", "abdel", "عبد"}


def short_name(full: str) -> str:
    """'Alaa Farez Mahmoud Esmaeil' → 'Alaa Esmaeil', 'Mohammed Al Asiri' stays, 'عبد الله سعد القحطاني' →
    'عبد الله القحطاني': first + family name is how a doctor is referred to on the phone."""
    words = full.split()
    if len(words) <= 2:
        return full
    first = words[:2] if words[0].lower() in _NAME_PREFIXES else words[:1]
    last = words[-2:] if words[-2].lower() in _NAME_PARTICLES else words[-1:]
    if len(first) + len(last) >= len(words):
        return full
    return " ".join(first + last)


@summarizer("mssql_get_TopFive_nearestClinic_have_doctorSlots", "mssql_get_nearestClinic_have_doctorSlots",
            "mssql_get_TopFive_availableDoctors_with_slots_byDate", "mssql_get_availableDoctors_with_slots_byDate")
def doctors_with_slots(data: Any) -> Any:
    if not isinstance(data, dict) or not ({"doctors", "clinics"} & set(data)):
        return data
    doctors = doctor_rows(data)
    for d in doctors:
        # "+N more, last …" so the model can answer "anything after 5 PM?" instead of guessing
        d["slots"] = {label_date(day): [label_time(t) for t in times[:MAX_TIMES_PER_DATE]] +
                      ([f"+{len(times) - MAX_TIMES_PER_DATE} more, last {label_time(times[-1])}"]
                       if len(times) > MAX_TIMES_PER_DATE else [])
                      for day, times in d["slots"].items()}
    return {"doctors": doctors, "page": data.get("page"), "has_next_page": data.get("hasNextPage", False)}
