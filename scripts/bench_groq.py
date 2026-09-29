"""Warm-connection latency benchmark for the Groq providers.

    python scripts/bench_groq.py [-n 5]

Measures what matters per voice turn: LLM time-to-first-token, TTS time for one short
sentence, and STT time for a ~3 s utterance at 16 kHz, for each Whisper model.
"""

import argparse
import asyncio
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from runtime.providers import AudioInput, TextDelta, create  # noqa: E402
from runtime.providers.audio import resample_pcm16  # noqa: E402

SYSTEM = "أنت موظف خدمة عملاء في مجموعة الدكتور سليمان الحبيب. رد بجملة قصيرة باللهجة النجدية."
USER = "أبي أحجز موعد عند دكتور جلدية"
SENTENCE = "حياك الله، وش المستشفى اللي تبي تحجز فيه؟"


def summary(name: str, xs: list[float]) -> None:
    print(f"  {name:<34} p50 {statistics.median(xs):6.0f} ms   min {min(xs):6.0f}   max {max(xs):6.0f}")


async def timed(coro_fn) -> float:
    t0 = time.perf_counter()
    await coro_fn()
    return (time.perf_counter() - t0) * 1000


async def main(n: int) -> None:
    llm, tts = create("llm", "groq"), create("tts", "groq")

    async def llm_ttft():
        async for ev in llm.stream([{"role": "system", "content": SYSTEM}, {"role": "user", "content": USER}]):
            if isinstance(ev, TextDelta):
                return

    audio: dict[str, bytes] = {}

    async def tts_one():
        pcm = b""
        async for c in tts.synthesize(SENTENCE, language="ar"):
            pcm += c.data
            audio["rate"] = c.sample_rate
        audio["pcm"] = pcm

    # Warm-up: TLS + connection pool
    await llm_ttft()
    await tts_one()
    clip = AudioInput(resample_pcm16(audio["pcm"], audio["rate"], 16000), 16000)
    print(f"LLM {llm.settings.model} · TTS {tts.settings.model_ar} · STT clip {clip.duration_ms / 1000:.1f}s @ 16 kHz\n")

    results: dict[str, list[float]] = {"LLM first token": [], "TTS one sentence (full audio)": []}
    stts = {m: create("stt", "groq", {"model": m}) for m in ("whisper-large-v3-turbo", "whisper-large-v3")}
    for m, stt in list(stts.items()):
        try:
            await stt.transcribe(clip)  # warm-up
            results[f"STT {m}"] = []
        except Exception as e:  # e.g. model not enabled for this Groq project
            print(f"  skipping {m}: {type(e).__name__}")
            del stts[m]

    for _ in range(n):
        results["LLM first token"].append(await timed(llm_ttft))
        results["TTS one sentence (full audio)"].append(await timed(tts_one))
        for m, stt in stts.items():
            results[f"STT {m}"].append(await timed(lambda: stt.transcribe(clip)))

    for name, xs in results.items():
        summary(name, xs)
    for m, stt in stts.items():
        print(f"\n  {m}: {(await stt.transcribe(clip)).text}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=5)
    asyncio.run(main(ap.parse_args().n))
