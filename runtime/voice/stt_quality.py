"""Is a transcript something the caller said, or Whisper hearing words in noise?

Whisper invents text on silence, background chatter and line noise — most often "Thank you." — and each invented
line would become a turn (live test 2026-09-28: 7 × "Thank you.", "." detected as Korean). Uses Whisper's own
per-segment confidence (verbose_json) plus known hallucination phrases.
"""

import re
from statistics import median
from typing import Any

import numpy as np

HALLUCINATIONS = re.compile(
    r"(ترجمة نانسي قنقر|اشتركوا? في القناة|شكرا للمشاهدة|thanks? for watching|subtitles? by|amara\.org|"
    r"^\W*$|^(you|\.+)$)", re.IGNORECASE)

# Short closings Whisper produces on noise; only trusted when Whisper is confident there was speech.
_THANKS = re.compile(r"^\W*(thank you( very much| so much)?|thanks( a lot)?|bye|شكرا|شكراً|مشكور|يعطيك العافي[هة])\W*$",
                     re.IGNORECASE)
_WORD = re.compile(r"[A-Za-z؀-ۿ]{2}|\d")

NO_SPEECH_PROB = 0.6      # Whisper's own silence rule: no_speech_prob above this …
LOW_LOGPROB = -0.5        # … and average log-probability below this (Groq noise probe: ~-0.7; speech ≥ -0.4)
THANKS_MAX_NO_SPEECH = 0.05   # live: invented "Thank you." 0.12–0.24, a real one 0.018
OVERLAP_MAX_NO_SPEECH = 0.05  # speech overlapping the agent's voice must be clearly speech (echo → "Thank you.")


def speech_stats(raw: dict[str, Any]) -> tuple[float | None, float | None]:
    """(highest no_speech_prob, mean avg_logprob) over the transcript's segments, or (None, None)."""
    segments = [s for s in (raw.get("segments") or []) if isinstance(s, dict)]
    if not segments:
        return None, None
    no_speech = max(float(s.get("no_speech_prob") or 0.0) for s in segments)
    logprob = sum(float(s.get("avg_logprob") or 0.0) for s in segments) / len(segments)
    return round(no_speech, 3), round(logprob, 3)


def speech_level_db(pcm16: bytes, frame: int = 320) -> float:
    """Loudness of the speech in an utterance (dBFS): 90th percentile of 20 ms frame RMS, so leading / trailing
    silence doesn't dilute it."""
    x = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32)
    if len(x) < frame:
        return -120.0
    frames = x[: len(x) // frame * frame].reshape(-1, frame)
    rms = np.sqrt((frames ** 2).mean(axis=1)) + 1e-9
    return round(float(20 * np.log10(np.percentile(rms, 90) / 32768.0)), 1)


class LevelGate:
    """Background voices (people talking near the caller) are real speech Whisper transcribes confidently — but
    they are much quieter than the caller at the microphone. Learns the caller's level from accepted utterances
    and rejects ones far below it."""

    def __init__(self, margin_db: float = 12.0, warmup: int = 2, window: int = 6) -> None:
        self.margin_db, self.warmup, self.window = margin_db, warmup, window
        self.levels: list[float] = []

    @property
    def caller_db(self) -> float | None:
        return median(self.levels) if len(self.levels) >= self.warmup else None

    def too_quiet(self, level_db: float) -> bool:
        ref = self.caller_db
        return ref is not None and level_db < ref - self.margin_db

    def accept(self, level_db: float) -> None:
        self.levels = (self.levels + [level_db])[-self.window:]


def _words(text: str) -> list[str]:
    return re.findall(r"[a-zء-ي]{2,}", text.lower())   # letters only (not the Arabic comma)


def echoes_prompt(text: str, prompt: str | None) -> bool:
    """Whisper sometimes returns its vocabulary prompt on noise ("Sulaiman Al Habib Medical Group. Appointment,
    clinic, Olaya…")."""
    words, vocab = _words(text), set(_words(prompt or ""))
    # echoes are long; a short answer made of hint words ("Olaya", "مستشفى العليا") is a real answer
    return len(words) >= 4 and bool(vocab) and sum(w in vocab for w in words) / len(words) >= 0.8


def noise_reason(text: str, raw: dict[str, Any] | None = None, prompt: str | None = None,
                 overlap: bool = False) -> str | None:
    """Why this transcript should be ignored, or None if it looks like real speech. `overlap`: the speech came
    while the agent was talking (or right after) — its own voice leaking back is the usual source, so be strict."""
    text = (text or "").strip()
    if not text or HALLUCINATIONS.search(text):
        return "hallucination"
    if echoes_prompt(text, prompt):
        return "prompt_echo"
    if not _WORD.search(text):
        return "no_words"
    no_speech, logprob = speech_stats(raw or {})
    if no_speech is not None and logprob is not None and no_speech > NO_SPEECH_PROB and logprob < LOW_LOGPROB:
        return "no_speech"
    if _THANKS.match(text) and (overlap or no_speech is None or no_speech > THANKS_MAX_NO_SPEECH):
        return "thanks_on_noise"
    if overlap and no_speech is not None and no_speech > OVERLAP_MAX_NO_SPEECH:   # providers without confidence: skip
        return "overlap_unsure"
    return None
