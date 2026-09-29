"""Caller gender for grammatically correct Arabic (تبي/تبين، حاب/حابة).

Evidence, strongest first:
  • self-reference in speech: "أنا تعبانة" (f) / "أنا تعبان" (m), "أنا حامل" (f), "زوجي" (f) / "زوجتي" (m)
  • voice classifier (Phase 7) — reported via GenderResolver.observe(..., source="voice")
  • patient's first name (weak: the caller may be calling for someone else)
Until confidence ≥ 0.7 the agent uses gender-neutral phrasing.
"""

import re
from dataclasses import dataclass

from runtime.text.arabic import normalize

# Masculine adjective / participle stems used in first-person self-reference (normalized forms).
_STEMS = set("""
تعبان مريض ساكن حاب متاكد خايف جاي مستعجل محتاج متضايق زعلان موظف متزوج مشغول مسافر فاضي موجود قاعد
رايح داخل طالع مصاب متعب مرتاح مبسوط متوتر قلقان عطشان جوعان نعسان حاس شاك متصل مراجع منوم رافع دافع
""".split())

_FEMALE_ONLY = re.compile(r"\b(حامل|حبلي|الحمل حقي|دورتي|زوجي|i'?m pregnant|my husband)\b")
_MALE_ONLY = re.compile(r"\b(زوجتي|مرتي|حرمتي|زوجتى|my wife)\b")
_EN_SELF = re.compile(r"\bi'?m (a )?(mother|mom|woman|lady)\b|\bi'?m (a )?(father|dad|man|gentleman)\b")

MALE_NAMES = set("""
محمد احمد عبدالله عبدالرحمن عبدالعزيز خالد فهد سعد سعود فيصل سلطان تركي ناصر عبدالمجيد ماجد نايف بندر
عمر علي حسن حسين ابراهيم يوسف يحيي موسي عيسي مصطفي حمزه اسامه طلحه عبيده معاويه عكرمه بدر ماهر راشد
مشعل منصور مساعد عادل وليد هاني سامي زياد رائد ياسر طلال عبدالاله عبدالملك عبدالكريم متعب مشاري نواف
""".split())
FEMALE_NAMES = set("""
فاطمه عائشه عايشه نوره ساره مريم هند منيره لطيفه حصه موضي الجوهره ريم لمياء اسماء هيفاء نجلاء شيماء
هدي منى ليلي سلمي نجوي رنا رهف جود لمي دانه غاده اروي امل بشري سعاد خلود عبير منال وعد شهد لولوه
""".split())


def gender_from_text(text: str) -> tuple[str, float] | None:
    t = normalize(text)
    if _FEMALE_ONLY.search(t):
        return "female", 0.85
    if _MALE_ONLY.search(t):
        return "male", 0.85
    if m := _EN_SELF.search(t):
        return ("female", 0.85) if m.group(2) else ("male", 0.85)
    tokens = t.split()
    for i, tok in enumerate(tokens):
        if tok not in ("انا", "اني", "ترا", "تراني", "لاني") or i + 1 >= len(tokens):
            continue
        nxt = tokens[i + 1]
        if nxt in _STEMS:
            return "male", 0.9
        if nxt.endswith("ه") and nxt[:-1] in _STEMS:
            return "female", 0.9
    return None


def gender_from_name(first_name: str | None) -> tuple[str, float] | None:
    if not first_name:
        return None
    n = normalize(first_name).replace(" ", "")
    if n in MALE_NAMES:
        return "male", 0.6
    if n in FEMALE_NAMES:
        return "female", 0.6
    if n.endswith(("ه", "اء")) and not n.startswith("عبد"):
        return "female", 0.4
    return None


@dataclass
class GenderResolver:
    gender: str | None = None
    confidence: float = 0.0
    source: str | None = None
    threshold: float = 0.7

    @property
    def known(self) -> str | None:
        return self.gender if self.confidence >= self.threshold else None

    def observe(self, result: tuple[str, float] | None, source: str) -> bool:
        """Adopt stronger evidence; returns True if the usable gender changed."""
        if result is None:
            return False
        before = self.known
        gender, conf = result
        if conf > self.confidence or (gender == self.gender and source != self.source):
            if gender == self.gender:
                conf = min(0.99, max(conf, self.confidence) + 0.05)
            self.gender, self.confidence, self.source = gender, conf, source
        return self.known != before
