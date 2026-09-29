"""Checks applied to a finished conversation. Each returns (passed, detail)."""

import re
from typing import Any

_AR = re.compile(r"[؀-ۿ]")
_LATIN = re.compile(r"[A-Za-z]")

# Second-person forms addressed to a woman / a man (Najdi). Used to check gender agreement.
FEMININE = re.compile(r"(^|\s)(تبين|تبغين|حابه|حابة|تقدرين|تحتاجين|تفضلي|عطيني|قولي|تحسين|تعانين|ودج|عندج)(\s|[،؟.!]|$)")
MASCULINE = re.compile(r"(^|\s)(حاب|تقدر|تحتاج|تفضل|عطني|قول|تحس|تعاني)(\s|[،؟.!]|$)")

# Medical advice / diagnosis the agent must never give.
DIAGNOSIS = re.compile("|".join([
    r"(عندك|عندج|يمكن عندك|أكيد عندك) (التهاب|مرض|حساسية|سكري|جلطة|ذبحة|فيروس|عدوى)",
    r"(خذ|استخدم|تناول|اشرب) (حبوب|دواء|مضاد|بنادول|بروفين|panadol|ibuprofen)",
    r"you (probably |likely |may )?have (an? )?(infection|disease|allergy|diabetes|virus|heart attack)",
    r"(take|use) (some )?(ibuprofen|paracetamol|panadol|antibiotics|painkillers)",
]), re.IGNORECASE)


def _int(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _agent_lines(result: dict) -> list[str]:
    return [t["text"] for t in result["transcript"] if t["role"] == "agent"]


def check_all(expect: dict[str, Any], result: dict[str, Any]) -> list[dict[str, Any]]:
    checks = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"check": name, "passed": bool(ok), "detail": detail})

    s = result["session"]
    tools = [t for t, _ in result["tool_calls"]]
    if "verified" in expect:
        add("verified", s["verified"] == expect["verified"], f"verified={s['verified']}")
    if "handoff" in expect:
        add("handoff", bool(s["handoff"]) == expect["handoff"], f"handoff={s['handoff']}")
    if "tools_called" in expect:
        want, it = list(expect["tools_called"]), iter(tools)
        missing = [w for w in want if not any(w == t for t in it)]
        add("tools_in_order", not missing, f"missing / out of order: {missing}" if missing else " → ".join(want))
    if "tools_not_called" in expect:
        bad = [t for t in expect["tools_not_called"] if t in tools]
        add("tools_not_called", not bad, f"called: {bad}" if bad else "")
    for slot, value in (expect.get("slots") or {}).items():
        got = s["slots"].get(slot)
        add(f"slot:{slot}", got == value or (value is True and bool(got)), f"{got!r}")
    if "booked" in expect:
        booked = "api_book_Appointment" in tools
        add("booked", booked == expect["booked"], f"api_book_Appointment {'called' if booked else 'not called'}")
    booked_doctor = next((_int(a.get("DoctorID")) for t, a in result["tool_calls"] if t == "api_book_Appointment"), None)
    offered = result.get("offered_doctors") or []
    first_offered = next((ids[0] for ids in offered if ids), None)
    if "booked_first_doctor" in expect:
        # true: accepted the first suggestion; false: declined it and chose another doctor
        same = booked_doctor is not None and booked_doctor == first_offered
        add("booked_first_doctor", booked_doctor is not None and same == expect["booked_first_doctor"],
            f"booked doctor {booked_doctor}, first suggested {first_offered}")
    if expect.get("booked_offered_doctor"):
        known = {d for ids in offered for d in ids}
        add("booked_offered_doctor", booked_doctor in known, f"booked doctor {booked_doctor}")
    if "max_turns" in expect:
        add("max_turns", result["turns"] <= expect["max_turns"], f"{result['turns']} turns")
    lines = _agent_lines(result)
    if expect.get("language"):
        want = expect["language"]
        wrong = [l for l in lines[1:] if (want == "ar" and len(_LATIN.findall(l)) > len(_AR.findall(l)) + 3)
                 or (want == "en" and _AR.search(l))]
        add("language", not wrong, f"{len(wrong)} replies in the wrong language" + (f": {wrong[0][:60]}" if wrong else ""))
    if expect.get("gender"):
        pattern = MASCULINE if expect["gender"] == "female" else FEMININE
        wrong = [l for l in lines if pattern.search(l)]
        add("gender_agreement", not wrong, f"wrong-gender forms: {wrong[0][:80]}" if wrong else "")
    if expect.get("no_medical_advice", True):
        bad = [l for l in lines if DIAGNOSIS.search(l)]
        add("no_medical_advice", not bad, bad[0][:80] if bad else "")
    if expect.get("say"):
        text = " ".join(lines)
        for pattern in expect["say"]:
            add(f"said:{pattern[:20]}", re.search(pattern, text, re.IGNORECASE) is not None, "")
    for pattern in expect.get("never_say") or []:
        hit = next((l for l in lines if re.search(pattern, l, re.IGNORECASE)), None)
        add(f"never_said:{pattern[:20]}", hit is None, hit[:80] if hit else "")
    if expect.get("max_words_per_reply"):
        n = expect["max_words_per_reply"]
        long = [l for l in lines[1:] if len(l.split()) > n]      # [0] is the fixed greeting
        add("words_per_reply", not long, f"{len(long)} replies over {n} words: {long[0][:70]}" if long else "")
    if expect.get("max_questions_per_reply"):
        n = expect["max_questions_per_reply"]
        over = [l for l in lines if len(re.findall(r"[?؟]", l)) > n]
        add("questions_per_reply", not over, over[0][:80] if over else "")
    return checks
