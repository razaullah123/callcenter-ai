"""Relative / absolute date expressions (Najdi Arabic + English) → ISO dates in Asia/Riyadh.

LLMs are unreliable at weekday arithmetic, so the harness resolves dates itself and hands the
LLM an explicit hint, e.g. "[date: 2026-10-01 (الخميس)]".
"""

import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from runtime.text.arabic import ascii_digits, normalize

from .numbers import spoken_digits, spoken_number

RIYADH = ZoneInfo("Asia/Riyadh")

WEEKDAYS_AR = ["الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]
WEEKDAYS_EN = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

_WEEKDAY_WORDS = {
    **{w: i for i, w in enumerate(["اثنين", "ثلاثاء", "اربعاء", "خميس", "جمعه", "سبت", "احد"])},
    **{w.lower(): i for i, w in enumerate(WEEKDAYS_EN)},
}
_MONTHS = {
    **{normalize(m): i for i, m in enumerate(
        ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"], 1)},
    "ابريل": 4, "اغسطس": 8, "يونيه": 6, "يوليه": 7,
    **{m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                   "september", "october", "november", "december"], 1)},
    **{m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                   "dec"], 1)},
}


def today_riyadh(now: datetime | None = None) -> date:
    return (now or datetime.now(RIYADH)).astimezone(RIYADH).date()


def describe(d: date, lang: str = "ar") -> str:
    return f"{d.isoformat()} ({WEEKDAYS_AR[d.weekday()] if lang == 'ar' else WEEKDAYS_EN[d.weekday()]})"


def _strip_article(tok: str) -> str:
    return tok[2:] if tok.startswith("ال") and len(tok) > 3 else tok


def resolve_date(text: str, today: date | None = None) -> date | None:
    """Best-effort single date in the text; None if there is no date expression."""
    today = today or today_riyadh()
    t = normalize(text)
    toks = t.split()
    bare = [_strip_article(x) for x in toks]

    if re.search(r"\bبعد (بكره|بكرا|باكر)\b|day after tomorrow", t):
        return today + timedelta(days=2)
    if re.search(r"\b(بكره|بكرا|باكر|tomorrow)\b", t):
        return today + timedelta(days=1)
    if re.search(r"\b(اليوم|today)\b", t):
        return today

    # ISO / numeric dates: 2026-10-01, 1/10/2026, 01-10-2026 (matched before punctuation is normalized away)
    raw = ascii_digits(text)
    if m := re.search(r"\b(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\b", raw):
        return _safe_date(int(m[1]), int(m[2]), int(m[3]))
    if m := re.search(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})\b", raw):
        return _safe_date(int(m[3]), int(m[2]), int(m[1]))

    # "بعد يومين / بعد ثلاث ايام / بعد اسبوع / in 3 days / in a week"
    if m := re.search(r"\b(?:بعد|in)\s+(?:(.+?)\s+)?(يومين|اسبوعين|ايام|يوم|اسبوع|days?|weeks?)\b", t):
        qty_text, unit = m[1] or "", m[2]
        n = 2 if unit in ("يومين", "اسبوعين") else int(spoken_digits(qty_text) or 1)
        return today + timedelta(days=n * 7 if unit.startswith(("اسبوع", "week")) else n)
    if re.search(r"\b(next week|الاسبوع الجاي|الاسبوع القادم)\b", t):
        return today + timedelta(days=7)

    # Weekday names: "الأحد", "يوم الخميس الجاي", "next monday"
    for i, tok in enumerate(bare):
        if tok in _WEEKDAY_WORDS:
            # Upcoming occurrence; the same weekday as today means next week ("الأحد" said on a Sunday).
            delta = (_WEEKDAY_WORDS[tok] - today.weekday()) % 7 or 7
            return today + timedelta(days=delta)

    # "15 أكتوبر", "١٥ اكتوبر ٢٠٢٦", "خمسطعش اكتوبر", "october 15"
    for i, tok in enumerate(bare):
        if tok in _MONTHS:
            month = _MONTHS[tok]
            before = spoken_digits(" ".join(toks[max(0, i - 2):i]))
            after = toks[i + 1:i + 7]
            if before and 1 <= int(before[-2:]) <= 31:          # "15 مارس 1990" / "خمسطعش مارس الف وتسعميه وتسعين"
                day, year_text = int(before[-2:]), " ".join(after)
            elif after and (d := spoken_digits(after[0])) and 1 <= int(d) <= 31:   # "october 20 2026"
                day, year_text = int(d), " ".join(after[1:])
            else:
                return None
            year = spoken_number(year_text) if year_text else None
            if year is not None and not 1900 <= year <= 2100:
                year = None
            if year is None:
                year = today.year
                candidate = _safe_date(year, month, day)
                if candidate and candidate < today:
                    year += 1
            return _safe_date(year, month, day)
    return None


def resolve_dob(text: str) -> date | None:
    """Gregorian date of birth (past date; year required)."""
    d = resolve_date(text, today=date(2100, 1, 1))  # disables relative words / year rollover
    return d if d and d.year >= 1900 and d <= today_riyadh() else None


def _safe_date(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None
