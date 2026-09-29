"""Spoken numbers → digits (Arabic incl. Najdi forms, and English), mobile-number normalization.

STT may return a phone number as digits ("0541234567"), Arabic-Indic digits ("٠٥٤١٢٣٤٥٦٧"),
or words ("صفر خمسة أربعة ..." / "خمسة وخمسين" / "zero five four double one ...").
"""

import re

from runtime.text.arabic import normalize

# Normalized forms (see runtime.text.arabic.normalize: ة→ه, أ→ا, ى→ي).
_UNITS = {
    "صفر": 0, "زيرو": 0,
    "واحد": 1, "وحده": 1, "احد": 1,
    "اثنين": 2, "اثنان": 2, "ثنين": 2, "اتنين": 2, "اثنينن": 2,
    "ثلاثه": 3, "ثلاث": 3, "ثلث": 3, "تلاته": 3, "تلات": 3,
    "اربعه": 4, "اربع": 4,
    "خمسه": 5, "خمس": 5,
    "سته": 6, "ست": 6,
    "سبعه": 7, "سبع": 7,
    "ثمانيه": 8, "ثمان": 8, "ثماني": 8, "ثمنيه": 8, "تمانيه": 8, "ثمن": 8,
    "تسعه": 9, "تسع": 9,
}
_TEENS = {
    "عشره": 10, "عشر": 10,
    "احدعش": 11, "حدعش": 11, "احدعشر": 11,
    "اطنعش": 12, "اثنعش": 12, "اثناعشر": 12, "اتناشر": 12,
    "ثلطعش": 13, "ثلاثطعش": 13, "ثلاثتعش": 13, "تلتاشر": 13,
    "اربعطعش": 14, "اربعتعش": 14, "اربعتاشر": 14,
    "خمسطعش": 15, "خمستعش": 15, "خمستاشر": 15,
    "ستطعش": 16, "ستعش": 16, "سطعش": 16, "ستاشر": 16,
    "سبعطعش": 17, "سبعتعش": 17, "سبعتاشر": 17,
    "ثمنطعش": 18, "ثمنتعش": 18, "تمنتاشر": 18,
    "تسعطعش": 19, "تسعتعش": 19, "تسعتاشر": 19,
}
_TENS = {"عشرين": 20, "ثلاثين": 30, "تلاتين": 30, "اربعين": 40, "خمسين": 50, "ستين": 60, "سبعين": 70,
         "ثمانين": 80, "تمانين": 80, "تسعين": 90}
_EN = {"zero": 0, "oh": 0, "o": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
       "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
       "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
       "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_REPEAT = {"double": 2, "triple": 3, "دبل": 2}


def _value(token: str) -> int | None:
    for table in (_UNITS, _TEENS, _TENS, _EN):
        if token in table:
            return table[token]
    return None


def spoken_digits(text: str) -> str:
    """Concatenate every number in the text, in order, as a digit string.

    "صفر خمسة اربعة ١٢٣" → "054123" · "خمسه وخمسين" → "55" · "double five" → "55"
    """
    out: list[str] = []
    tokens = normalize(text).split()
    i = 0
    repeat = 1
    while i < len(tokens):
        tok = tokens[i]
        if tok.isdigit():
            out.append(tok * repeat)
            repeat = 1
            i += 1
            continue
        if tok in _REPEAT:
            repeat = _REPEAT[tok]
            i += 1
            continue
        stem = tok[1:] if tok.startswith("و") and _value(tok[1:]) is not None and _value(tok) is None else tok
        v = _value(stem)
        if v is None:
            i += 1
            continue
        # unit + "و" + tens  → compound ("خمسه وخمسين" = 55)
        if v < 10 and i + 1 < len(tokens):
            nxt = tokens[i + 1]
            nxt_v = _value(nxt[1:]) if nxt.startswith("و") else None
            if nxt_v is not None and nxt_v >= 20 and nxt_v % 10 == 0:
                out.append(str(nxt_v + v) * repeat)
                repeat = 1
                i += 2
                continue
        # English "fifty five"
        if v >= 20 and v % 10 == 0 and i + 1 < len(tokens) and tokens[i + 1] in _EN and _EN[tokens[i + 1]] < 10:
            out.append(str(v + _EN[tokens[i + 1]]) * repeat)
            repeat = 1
            i += 2
            continue
        out.append(str(v) * repeat)
        repeat = 1
        i += 1
    return "".join(out)


_HUNDREDS = {"ميه": 100, "مائه": 100, "مية": 100, "ميتين": 200, "مئتين": 200, "ثلاثميه": 300, "تلتميه": 300,
             "ثلثميه": 300, "اربعميه": 400, "خمسميه": 500, "ستميه": 600, "سبعميه": 700, "ثمانميه": 800,
             "ثمنميه": 800, "تمنميه": 800, "تسعميه": 900, "hundred": 100}
_THOUSANDS = {"الف": 1000, "الفين": 2000, "thousand": 1000}


def spoken_number(text: str) -> int | None:
    """A single cardinal number by addition, for years: "الف وتسعميه وتسعين" → 1990,
    "الفين وخمسه" → 2005, "two thousand ten" → 2010, "nineteen ninety" → 1990 (pairs), "1990" → 1990."""
    tokens = [t[1:] if t.startswith("و") and len(t) > 2 and t not in _UNITS else t for t in normalize(text).split()]
    if any(t.isdigit() for t in tokens):
        return int("".join(t for t in tokens if t.isdigit()))
    total, current, seen = 0, 0, False
    for tok in tokens:
        if tok in _THOUSANDS:
            total += (current or 1) * _THOUSANDS[tok] if tok == "thousand" else _THOUSANDS[tok]
            current, seen = 0, True
        elif tok in _HUNDREDS:
            current = (current or 1) * 100 if tok == "hundred" else current + _HUNDREDS[tok]
            seen = True
        elif (v := _value(tok)) is not None:
            current += v
            seen = True
    if not seen:
        return None
    value = total + current
    # English year pairs: "nineteen ninety" → 19 + 90 = 109 → read as 1990
    if total == 0 and value < 200:
        pairs = spoken_digits(text)
        if len(pairs) == 4:
            return int(pairs)
    return value


_MOBILE = re.compile(r"(?:00966|966)?0?(5\d{8})")


def normalize_mobile(text: str) -> str | None:
    """Saudi mobile in local format 05XXXXXXXX, or None. Accepts +9665…, 9665…, 5…, 05…, spoken digits.
    All digits in the text must form the number: a longer / garbled digit string (e.g. STT repeating
    zeros) returns None so the agent asks again instead of looking up a wrong number."""
    digits = spoken_digits(text)
    m = _MOBILE.fullmatch(digits)
    return f"0{m.group(1)}" if m else None


def extract_code(text: str, min_len: int = 4, max_len: int = 8) -> str | None:
    """A short numeric code (OTP) from speech, e.g. "واحد اثنين ثلاثه اربعه" → "1234"."""
    digits = spoken_digits(text)
    return digits if min_len <= len(digits) <= max_len else None
