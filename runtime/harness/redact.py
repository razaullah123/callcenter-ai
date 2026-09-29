"""PII redaction for events (logs, live console). Registered as an EventBus redactor."""

import re
from typing import Any

from runtime.events import Event

_SENSITIVE_KEY = re.compile(r"(mobile|phone|otp|dob|birth|national|iqama|passport|email|name|address)", re.I)
_LONG_DIGITS = re.compile(r"(?<!\d)(\+?\d[\d\s-]{6,}\d)(?!\d)")
_ARABIC_DIGITS = re.compile(r"[٠-٩۰-۹]{7,}")


_DATE_LIKE = re.compile(r"^\d{4}-\d{1,2}-\d{1,2}$|^\d{1,2}[-/]\d{1,2}[-/]\d{4}$")


def _mask_digits(m: re.Match) -> str:
    raw = m.group(1)
    if _DATE_LIKE.match(raw.strip()):
        return raw
    return "•" * 4 + re.sub(r"\D", "", raw)[-2:]


def _mask_text(text: str) -> str:
    text = _LONG_DIGITS.sub(_mask_digits, text)
    return _ARABIC_DIGITS.sub("••••", text)


def _mask(value: Any, key: str = "") -> Any:
    if key and _SENSITIVE_KEY.search(key) and not isinstance(value, (dict, list, bool)) and value is not None:
        return "•••"
    if isinstance(value, dict):
        return {k: _mask(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [_mask(v, key) for v in value]
    if isinstance(value, str):
        return _mask_text(value)
    return value


def redact_event(event: Event) -> Event:
    event.data = _mask(event.data)
    return event
