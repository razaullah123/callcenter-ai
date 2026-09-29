"""Text normalization before TTS: numbers and clock times → words the TTS reads correctly.

- Numbers of 4+ digits (phone endings, OTP codes, appointment numbers) are read digit by digit — "ends with 2968"
  → "two nine six eight" — except a year after a month name ("November 2026").
- Clock times keep their own AM / PM marker: "8:15 AM" → "eight fifteen a.m.", "8:15 صباحاً" → "ثمانية وربع صباحاً".
The LLM writes times in digits with AM / PM (صباحاً / مساءً); this turns them into speech.
"""

import re

from runtime.text.arabic import ascii_digits

_ONES = ["صفر", "واحد", "اثنين", "ثلاثة", "أربعة", "خمسة", "ستة", "سبعة", "ثمانية", "تسعة", "عشرة",
         "إحدعش", "اطنعش", "ثلطعش", "أربعطعش", "خمسطعش", "ستطعش", "سبعطعش", "ثمنطعش", "تسعطعش"]
_TENS = {20: "عشرين", 30: "ثلاثين", 40: "أربعين", 50: "خمسين", 60: "ستين", 70: "سبعين", 80: "ثمانين", 90: "تسعين"}
_HUNDREDS = {1: "مية", 2: "ميتين", 3: "ثلاثمية", 4: "أربعمية", 5: "خمسمية", 6: "ستمية", 7: "سبعمية", 8: "ثمانمية",
             9: "تسعمية"}
_HOURS = ["اطنعش", "وحدة", "ثنتين", "ثلاث", "أربع", "خمس", "ست", "سبع", "ثمان", "تسع", "عشر", "إحدعش"]

_EN_DIGITS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
_EN_ONES = _EN_DIGITS + ["ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen",
                         "eighteen", "nineteen"]
_EN_TENS = {20: "twenty", 30: "thirty", 40: "forty", 50: "fifty"}

_MONTHS = (r"january|february|march|april|may|june|july|august|september|october|november|december|"
           r"يناير|فبراير|مارس|أبريل|ابريل|مايو|يونيو|يوليو|أغسطس|اغسطس|سبتمبر|أكتوبر|اكتوبر|نوفمبر|ديسمبر")


def number_words(n: int) -> str:
    if n < 20:
        return _ONES[n]
    if n < 100:
        tens, ones = divmod(n, 10)
        return _TENS[tens * 10] if ones == 0 else f"{_ONES[ones]} و{_TENS[tens * 10]}"
    if n < 1000:
        h, rest = divmod(n, 100)
        return _HUNDREDS[h] + (f" و{number_words(rest)}" if rest else "")
    if n < 1_000_000:
        th, rest = divmod(n, 1000)
        head = "ألف" if th == 1 else "ألفين" if th == 2 else f"{number_words(th)} آلاف" if th < 11 else f"{number_words(th)} ألف"
        return head + (f" و{number_words(rest)}" if rest else "")
    return " ".join(_ONES[int(d)] for d in str(n))   # long ids: digit by digit


# ---------------------------------------------------------------- clock times

_AR_AM, _AR_PM = "صباحاً", "مساءً"
_AR_PERIOD = r"صباحاً|صباحا|الصبح|ص|مساءً|مساء|مساءا|المساء|الظهر|العصر|المغرب|بالليل|م"
_EN_PERIOD = r"a\.?\s?m\.?|p\.?\s?m\.?"


def clock_words(hour: int, minute: int, period: str | None = None) -> str:
    """Arabic: 08:15 → "ثمان وربع صباحاً", 15:30 → "ثلاث ونص مساءً" (24-h input, or 12-h + period)."""
    pm = hour >= 12 if period is None else period
    h = _HOURS[hour % 12]
    m = {0: "", 15: " وربع", 30: " ونص", 45: " إلا ربع"}.get(minute)
    if minute == 45:
        h = _HOURS[(hour + 1) % 12]
    if m is None:
        m = f" و{number_words(minute)} دقيقة" if minute < 20 else f" و{number_words(minute)}"
    return f"{h}{m} {_AR_PM if pm else _AR_AM}"


def clock_words_en(hour: int, minute: int, period: str | None = None) -> str:
    """English: 08:15 → "eight fifteen a.m.", 8:05 PM → "eight oh five p.m.", 12:00 → "twelve p.m."."""
    pm = hour >= 12 if period is None else period
    h = _EN_ONES[(hour % 12) or 12]
    if minute == 0:
        m = ""
    elif minute < 10:
        m = f" oh {_EN_DIGITS[minute]}"
    elif minute < 20:
        m = f" {_EN_ONES[minute]}"
    else:
        tens, ones = divmod(minute, 10)
        m = f" {_EN_TENS[tens * 10]}" + (f" {_EN_DIGITS[ones]}" if ones else "")
    return f"{h}{m} {'p.m.' if pm else 'a.m.'}"


def _is_pm(word: str | None) -> bool | None:
    if not word:
        return None
    w = word.lower().replace(".", "").replace(" ", "")
    if w in ("am", "صباحاً", "صباحا", "الصبح", "ص"):
        return False
    return True    # pm, مساء, الظهر, العصر, المغرب, بالليل, م


# 8:15 [AM]  |  8 AM  (a bare "8" is a count, not a time)
_EN_TIME = re.compile(rf"\b(\d{{1,2}}):(\d{{2}})(?:\s*({_EN_PERIOD}))?(?![\w.])|\b(\d{{1,2}})\s*({_EN_PERIOD})(?![\w.])",
                      re.IGNORECASE)
# "not followed by a letter" — Arabic punctuation (، ؟) may follow the period word
_AR_TIME = re.compile(rf"(?<!\d)(\d{{1,2}}):(\d{{2}})(?:\s*({_AR_PERIOD}))?(?![A-Za-zء-ْ])|"
                      rf"(?<!\d)(\d{{1,2}})\s*({_AR_PERIOD})(?![A-Za-zء-ْ])")


def _time_sub(m: re.Match, words) -> str:
    if m[1] is not None:
        hour, minute, period = int(m[1]), int(m[2]), m[3]
    else:
        hour, minute, period = int(m[4]), 0, m[5]
    if hour > 23 or minute > 59:
        return m[0]
    pm = _is_pm(period)
    if pm is not None and hour > 12:          # "14:30 PM"
        pm = True
    if pm is not None:
        hour = hour % 12 + (12 if pm else 0)
    return words(hour, minute)


# ---------------------------------------------------------------- long numbers: digit by digit

_LONG = re.compile(rf"(?<![\d:])(?:(?P<month>(?:{_MONTHS})\.?,?\s+))?(?P<num>\d{{4,}})(?![\d:])", re.IGNORECASE)


def _digits(m: re.Match, names: list[str], sep: str) -> str:
    num = m["num"]
    if m["month"] and len(num) == 4 and num[:2] in ("19", "20"):
        return m[0]                                       # "November 2026": a year
    # a short pause after each digit: easier to follow (and write down) on the phone
    return (m["month"] or "") + sep.join(names[int(d)] for d in num)


_JOINED_PREFIX = re.compile(r"(\S*ـ)\s+(?=\d{4,})")    # "بـ 2968" (tatweel-joined prefix before a number)
_NUM = re.compile(r"\d+")
_AR = re.compile(r"[؀-ۿ]")


def normalize_for_tts(text: str, language: str) -> str:
    t = ascii_digits(text)
    if language == "ar" and _AR.search(t):
        t = _AR_TIME.sub(lambda m: _time_sub(m, clock_words), t)
        # "ينتهي بـ 2968": the TTS runs "بـ" into the first digit (heard as "MIM968") — a pause mark keeps it apart
        t = _JOINED_PREFIX.sub(lambda m: m[1] + ": ", t)
        t = _LONG.sub(lambda m: _digits(m, _ONES, "، "), t)
        t = _NUM.sub(lambda m: number_words(int(m[0])), t)
    else:
        t = _EN_TIME.sub(lambda m: _time_sub(m, clock_words_en), t)
        t = _LONG.sub(lambda m: _digits(m, _EN_DIGITS, ", "), t)
    return re.sub(r"\s+", " ", t).strip()
