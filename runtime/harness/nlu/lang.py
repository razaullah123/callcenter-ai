"""Call language: Arabic (LanguageID 1) or English (LanguageID 2).

The first utterance with real words decides the call language. It switches only if the caller
explicitly asks, or speaks the other language for two consecutive turns.
"""

import re

_AR = re.compile(r"[؀-ۿ]")
_LATIN = re.compile(r"[A-Za-z]")

_ASK_ENGLISH = re.compile(r"\b(english|انجليزي|انقليزي|بالانجليزي|بالإنجليزي)\b", re.IGNORECASE)
_ASK_ARABIC = re.compile(r"\b(arabic|عربي|بالعربي)\b", re.IGNORECASE)

LANGUAGE_ID = {"ar": 1, "en": 2}


def detect(text: str, stt_language: str | None = None) -> str | None:
    ar, lat = len(_AR.findall(text)), len(_LATIN.findall(text))
    if ar == 0 and lat == 0:
        return stt_language if stt_language in LANGUAGE_ID else None
    return "ar" if ar >= lat else "en"


class LanguageTracker:
    def __init__(self, default: str = "ar") -> None:
        self.language = default
        self.decided = False
        self._other_streak = 0

    @property
    def language_id(self) -> int:
        return LANGUAGE_ID[self.language]

    def update(self, text: str, stt_language: str | None = None) -> bool:
        """Returns True if the call language changed."""
        if _ASK_ENGLISH.search(text) and self.language != "en" and re.search(r"(تكلم|كلم|speak|talk|please)", text, re.I):
            return self._set("en")
        if _ASK_ARABIC.search(text) and self.language != "ar":
            return self._set("ar")
        lang = detect(text, stt_language)
        if lang is None:
            return False
        if not self.decided:
            self.decided = True
            return self._set(lang) if lang != self.language else False
        if lang == self.language:
            self._other_streak = 0
            return False
        self._other_streak += 1
        return self._set(lang) if self._other_streak >= 2 else False

    def _set(self, lang: str) -> bool:
        changed = lang != self.language
        self.language, self.decided, self._other_streak = lang, True, 0
        return changed
