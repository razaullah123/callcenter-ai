"""Arabic / English text normalization for matching spoken input against names.

Speech-to-text output varies in hamza, ta-marbuta, alef-maqsura, diacritics and digits;
normalizing both sides makes "مستشفى الحمرا" match "مستشفى الحمراء".
"""

import re

_DIACRITICS = re.compile(r"[ؐ-ًؚ-ٰٟۖ-ۭـ]")  # tashkeel, Quranic marks, tatweel
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_SPACES = re.compile(r"\s+")

_CHAR_MAP = str.maketrans({
    "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا",
    "ة": "ه", "ى": "ي", "ؤ": "و", "ئ": "ي",
    # Arabic-Indic and Persian digits → ASCII
    **{chr(0x0660 + i): str(i) for i in range(10)},
    **{chr(0x06F0 + i): str(i) for i in range(10)},
})


def normalize(text: str) -> str:
    """Lowercase, strip diacritics / punctuation, unify letter variants and digits."""
    text = _DIACRITICS.sub("", text or "").translate(_CHAR_MAP).lower()
    text = _PUNCT.sub(" ", text.replace("_", " "))
    return _SPACES.sub(" ", text).strip()


def strip_article(token: str) -> str:
    """Drop the Arabic definite article ("ال") and English "al"/"el" prefixes for comparison."""
    if token.startswith("ال") and len(token) > 3:
        return token[2:]
    if token.startswith(("al", "el")) and len(token) > 4 and token[2] != " ":
        return token[2:]
    return token


def core_tokens(text: str, stopwords: frozenset[str] = frozenset()) -> str:
    """Normalized tokens without stopwords or articles, joined by spaces."""
    tokens = []
    for tok in normalize(text).split():
        if tok in stopwords or tok in ("al", "el"):
            continue
        tok = strip_article(tok)
        if tok and tok not in stopwords:
            tokens.append(tok)
    return " ".join(tokens)


_DIGITS = str.maketrans({**{chr(0x0660 + i): str(i) for i in range(10)}, **{chr(0x06F0 + i): str(i) for i in range(10)}})


def ascii_digits(text: str) -> str:
    """Arabic-Indic / Persian digits → ASCII, everything else unchanged."""
    return (text or "").translate(_DIGITS)
