"""LLM extraction for the details the call is waiting for — when the fast pattern parsers find nothing.

Speech-to-text garbles exactly what matters (split / joined digits, Najdi number words, misheard words, Arabic
month names), so a regex miss must not mean the caller's answer is lost. Each field gets one tight prompt that
returns JSON only; the result is validated deterministically (10 digits starting 05, 4–6 digit code, a real
date, one of the offered times) and anything unsure is dropped — then the agent simply asks again.
"""

import json
import re
import time
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from runtime.providers import LLMProvider, TextDelta

from .numbers import normalize_mobile

# ---------------------------------------------------------------- when is it worth a call?

_NUMBER_WORDS = re.compile(
    r"\d|[٠-٩]|صفر|واحد|اثن|ثنين|ثلاث|ثلث|اربع|أربع|خمس|ست|سبع|ثمان|تسع|عشر|مية|ميه|الف|ألف|"
    r"\b(zero|oh|one|two|three|four|five|six|seven|eight|nine|ten|double|triple)\b", re.IGNORECASE)
_DATE_WORDS = re.compile(
    r"\d|[٠-٩]|يوم|بكر|بعد|الجاي|القادم|الاسبوع|الأسبوع|شهر|الاحد|الأحد|الاثنين|الإثنين|الثلاثاء|الاربعاء|الأربعاء|"
    r"الخميس|الجمعة|السبت|يناير|فبراير|مارس|ابريل|أبريل|مايو|يونيو|يوليو|اغسطس|أغسطس|سبتمبر|اكتوبر|أكتوبر|نوفمبر|"
    r"ديسمبر|tomorrow|today|next|week|month|monday|tuesday|wednesday|thursday|friday|saturday|sunday|january|"
    r"february|march|april|may|june|july|august|september|october|november|december|\b\d", re.IGNORECASE)


def numberish(text: str) -> bool:
    return bool(_NUMBER_WORDS.search(text))


def dateish(text: str) -> bool:
    return bool(_DATE_WORDS.search(text))


# ---------------------------------------------------------------- prompts (one per field, JSON only)

_STT = ("The text is a speech-to-text transcript of a phone caller (Arabic — often Saudi Najdi — or English). It may "
        "be garbled: digits spoken as words (صفر، واحد، اثنين/ثنين، ثلاثة/ثلاث، أربعة، خمسة، ستة، سبعة، ثمانية/ثمان، "
        "تسعة / zero, oh, one …), pairs spoken as numbers (ثمنطعش = 18, خمسطعش = 15, واحد وتسعين = 91, تسعة وعشرين "
        "= 29, sixty eight = 68), digits split or joined oddly, 'double five', repeated or misheard words, filler "
        "words, self-corrections ('sorry', 'لا قصدي', starting over). Never invent anything the caller did not say.")

PROMPTS = {
    "mobile": _STT + """
Extract the Saudi mobile number the caller said. A Saudi mobile is 10 digits starting with 05; +9665… / 009665…
/ 9665… / 5XXXXXXXX (9 digits) mean the same number → write it as 05XXXXXXXX. If the caller corrects themselves
or starts over, use their final complete number and ignore everything before it (other numbers, false starts).
Reply with JSON only: {"mobile": "05XXXXXXXX" or null, "confidence": "high" or "low"}.
null if the caller didn't say all 10 digits; "low" if any digit is a guess.""",

    "otp": _STT + """
Extract the verification code (OTP) the caller read out: 4 to 6 digits, in the order spoken.
Reply with JSON only: {"code": "digits" or null, "confidence": "high" or "low"}.
null if there is no complete code; "low" if any digit is a guess.""",

    "dob_name": _STT + """
The caller was asked for their date of birth (Gregorian) and first name. Extract both.
Reply with JSON only: {"date_of_birth": "YYYY-MM-DD" or null, "first_name": "name as said" or null,
"confidence": "high" or "low"}.""",

    "date": _STT + """
The caller is choosing the day for a hospital appointment. Today is {today} ({weekday}).
Extract the day they want. Relative days ("بكرة", "الأحد الجاي", "next Tuesday", "بعد يومين") → the actual date.
If they want the earliest / nearest available appointment instead of a specific day, set "earliest": true —
also when speech-to-text garbled it ("موعد مطاح", "موعد مطه", "أغري موعد" for "أقرب موعد متاح"; "any day",
"whatever is first", "أي يوم", "أول موعد").
Reply with JSON only: {"date": "YYYY-MM-DD" or null, "earliest": true or false, "confidence": "high" or "low"}.""",

    "time_choice": _STT + """
The agent just offered these appointment times: {offered}.
Which one did the caller choose? Times may be said as "الثانية ونص", "اثنين ونص الظهر", "two thirty", "the first
one", "the last one", "8:15". Answer with a time from the list only.
Reply with JSON only: {"time": "HH:MM" (24-hour, from the list) or null, "confidence": "high" or "low"}.""",
}


@dataclass
class Extraction:
    field: str
    value: Any                     # validated value, or None
    raw: dict[str, Any]
    latency_ms: float


def _json(text: str) -> dict[str, Any]:
    for m in re.finditer(r"\{[^{}]*\}", text, re.DOTALL):
        try:
            obj = json.loads(m.group(0))
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue
    return {}


# ---------------------------------------------------------------- validation (the model never has the last word)

def validate(field: str, obj: dict[str, Any], *, today: date, offered: set[str] | None = None) -> Any:
    if str(obj.get("confidence", "high")).lower() == "low":
        return None
    if field == "mobile":
        m = str(obj.get("mobile") or "")
        return m if re.fullmatch(r"05\d{8}", m) and normalize_mobile(m) == m else None
    if field == "otp":
        c = re.sub(r"\D", "", str(obj.get("code") or ""))
        return c if 4 <= len(c) <= 6 else None
    if field == "dob_name":
        try:
            dob = date.fromisoformat(str(obj.get("date_of_birth") or "")[:10])
        except ValueError:
            dob = None
        if dob and not (date(1900, 1, 1) <= dob < today):
            dob = None
        name = str(obj.get("first_name") or "").strip() or None
        return {"date_of_birth": dob, "first_name": name} if (dob or name) else None
    if field == "date":
        if obj.get("earliest") is True:
            return "earliest"
        try:
            d = date.fromisoformat(str(obj.get("date") or "")[:10])
        except ValueError:
            return None
        return d if today <= d <= today + timedelta(days=366) else None
    if field == "time_choice":
        t = str(obj.get("time") or "")
        return t if offered and t in offered else None
    return None


async def extract(llm: LLMProvider, field: str, text: str, *, today: date, offered: set[str] | None = None,
                  context: str = "") -> Extraction:
    """One tight, JSON-only extraction call for `field` (validated). Errors → no value, never an exception."""
    t0 = time.perf_counter()
    # plain replacement, not str.format: the prompts contain literal JSON braces
    prompt = (PROMPTS[field].replace("{today}", today.isoformat()).replace("{weekday}", today.strftime("%A"))
              .replace("{offered}", ", ".join(sorted(offered or []))))
    user = (f"{context}\nCaller said: {text}" if context else f"Caller said: {text}")
    out = ""
    try:
        async for ev in llm.stream([{"role": "system", "content": prompt}, {"role": "user", "content": user}],
                                   temperature=0, max_tokens=700):
            if isinstance(ev, TextDelta):
                out += ev.text
    except Exception as e:                            # a failed extraction just means "ask again"
        return Extraction(field, None, {"error": repr(e)[:120]}, round((time.perf_counter() - t0) * 1000, 1))
    obj = _json(out)
    return Extraction(field, validate(field, obj, today=today, offered=offered), obj,
                      round((time.perf_counter() - t0) * 1000, 1))
