"""Pre-synthesized audio for fixed phrases (greeting, fillers, handoff, fallback): zero TTS latency.

Cached on disk per (model, voice, text) so restarts don't re-synthesize.
"""

import asyncio
import hashlib
import logging
from pathlib import Path

from runtime.config import ROOT_DIR
from runtime.harness.prompts import Phrases
from runtime.providers import TTSProvider
from runtime.providers.audio import pcm16_to_wav, wav_to_pcm16

from .normalize import normalize_for_tts

log = logging.getLogger(__name__)
CACHE_DIR = ROOT_DIR / "models" / "phrases"


def fixed_phrases(ph: Phrases | None = None, languages: tuple[str, ...] = ("ar", "en")) -> list[tuple[str, str]]:
    """The agent's fixed lines, per language — synthesized once in the agent's own voice."""
    ph = ph or Phrases()
    out = []
    for lang in languages:
        out += [(lang, ph.GREETING[lang]), (lang, ph.HANDOFF[lang]), (lang, ph.HANDOFF_SHORT[lang]),
                (lang, ph.FALLBACK[lang]), (lang, ph.RECORDING_NOTICE[lang])]
        out += [(lang, f) for f in ph.FILLER[lang]]
        out += [(lang, ph.STILL_WORKING[lang])] + [(lang, f[lang]) for f in ph.SLOW_TOOL_FILLER.values()]
    return out


class PhraseCache:
    def __init__(self, tts: TTSProvider, directory: Path = CACHE_DIR, phrases: Phrases | None = None) -> None:
        self.tts = tts
        self.phrases = phrases
        self.dir = directory
        self._mem: dict[tuple[str, str], tuple[bytes, int]] = {}

    def get(self, language: str, text: str) -> tuple[bytes, int] | None:
        return self._mem.get((language, text.strip()))

    def _path(self, language: str, text: str) -> Path:
        s = self.tts.settings
        model = getattr(s, "model_en" if language == "en" else "model_ar", "")
        voice = getattr(s, "voice_en" if language == "en" else "voice_ar", "")
        key = hashlib.sha1(f"{model}|{voice}|{language}|{text}".encode()).hexdigest()[:16]
        return self.dir / f"{language}_{key}.wav"

    async def warm(self, phrases: list[tuple[str, str]] | None = None, concurrency: int = 3) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        sem = asyncio.Semaphore(concurrency)

        async def one(language: str, text: str) -> None:
            path = self._path(language, text)
            if path.exists():
                self._mem[(language, text)] = wav_to_pcm16(path.read_bytes())
                return
            async with sem:
                try:
                    pcm, rate = b"", 24000
                    async for chunk in self.tts.synthesize(normalize_for_tts(text, language), language=language):
                        pcm += chunk.data
                        rate = chunk.sample_rate
                    path.write_bytes(pcm16_to_wav(pcm, rate))
                    self._mem[(language, text)] = (pcm, rate)
                except Exception as e:
                    log.warning("phrase pre-synthesis failed (%s): %r", text[:30], e)

        await asyncio.gather(*(one(lang, text) for lang, text in (phrases or fixed_phrases(self.phrases))))
        log.info("phrase cache: %d phrases ready", len(self._mem))
