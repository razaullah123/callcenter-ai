"""Live round-trip check of the Groq providers: LLM → TTS → STT, with latencies.

    python scripts/smoke_groq.py            # Arabic
    python scripts/smoke_groq.py --en       # English

Writes the synthesized audio to logs/smoke_<lang>.wav so you can listen to the voice.
"""

import argparse
import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252, which can't print Arabic

from runtime.providers import AudioInput, TextDelta, create  # noqa: E402
from runtime.providers.audio import pcm16_to_wav  # noqa: E402

PROMPTS = {
    "ar": ("أنت موظف خدمة عملاء في مجموعة الدكتور سليمان الحبيب. تكلم باللهجة النجدية وبجملة وحدة قصيرة.",
           "أبي أحجز موعد عند دكتور جلدية"),
    "en": ("You are a customer care agent at Dr. Sulaiman Al Habib Group. Reply in one short sentence.",
           "I want to book a dermatology appointment"),
}


def ms(t0: float) -> str:
    return f"{(time.perf_counter() - t0) * 1000:.0f} ms"


async def main(lang: str) -> None:
    system, user = PROMPTS[lang]
    llm, tts, stt = create("llm", "groq"), create("tts", "groq"), create("stt", "groq")
    print(f"models: llm={llm.settings.model}  stt={stt.settings.model}  "
          f"tts={tts.settings.model_en if lang == 'en' else tts.settings.model_ar}")

    t0 = time.perf_counter()
    first = None
    reply = ""
    async for ev in llm.stream([{"role": "system", "content": system}, {"role": "user", "content": user}]):
        if isinstance(ev, TextDelta):
            first = first or ms(t0)
            reply += ev.text
    print(f"\nLLM   first token {first}, total {ms(t0)}\n      {reply.strip()}")

    t0 = time.perf_counter()
    pcm, rate, first = b"", 24000, None
    async for chunk in tts.synthesize(reply, language=lang):
        first = first or ms(t0)
        pcm += chunk.data
        rate = chunk.sample_rate
    out = ROOT / "logs" / f"smoke_{lang}.wav"
    out.parent.mkdir(exist_ok=True)
    out.write_bytes(pcm16_to_wav(pcm, rate))
    print(f"\nTTS   first audio {first}, total {ms(t0)}  ({len(pcm) / 2 / rate:.1f}s audio @ {rate} Hz) → {out}")

    t0 = time.perf_counter()
    t = await stt.transcribe(AudioInput(pcm, rate))
    print(f"\nSTT   {ms(t0)}  language={t.language}\n      {t.text}")

    t0 = time.perf_counter()
    [mu] = [c async for c in tts.synthesize("تم", language=lang, encoding="mulaw", sample_rate=8000)]
    print(f"\nTTS μ-law 8 kHz (IVR format): {len(mu.data)} bytes in {ms(t0)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--en", action="store_true")
    asyncio.run(main("en" if ap.parse_args().en else "ar"))
