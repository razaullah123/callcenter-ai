"""Simulated phone caller for the voice server: speaks scripted lines (via TTS), streams audio in real
time like an IVR, listens to the agent, and reports latency per turn.

    python -m runtime.server                     # in another terminal
    python scripts/voice_sim.py scripts/flows/voice_en.txt [--ivr]   # --ivr: μ-law 8 kHz like telephony

Lines: caller utterances; the language of each line picks the caller's TTS voice.
"""

import argparse
import asyncio
import base64
import json
import re
import sys
import time
from pathlib import Path

import websockets

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from runtime.providers import create  # noqa: E402
from runtime.providers.audio import pcm16_to_mulaw, resample_pcm16  # noqa: E402

AR = re.compile(r"[؀-ۿ]")


async def synth(tts, text: str, rate: int) -> bytes:
    lang = "ar" if AR.search(text) else "en"
    voice = None if lang == "ar" else "troy"   # a different voice than the agent's
    pcm, src_rate = b"", 24000
    async for c in tts.synthesize(text, language=lang, voice=voice):
        pcm, src_rate = pcm + c.data, c.sample_rate
    return resample_pcm16(pcm, src_rate, rate)


async def main(script: Path, url: str, ivr: bool) -> None:
    rate, enc = (8000, "mulaw") if ivr else (16000, "pcm16")
    frame = rate // 50 * (1 if ivr else 2)            # 20 ms
    lines = [l.strip() for l in script.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]
    tts = create("tts", "groq")
    print(f"synthesizing {len(lines)} caller lines…")
    # "DTMF:0551234567#" lines press keys instead of speaking
    clips = [l if l.startswith("DTMF:") else await synth(tts, l, rate) for l in lines]
    if ivr:
        clips = [c if isinstance(c, str) else pcm16_to_mulaw(c) for c in clips]

    async with websockets.connect(url, max_size=None) as ws:
        await ws.send(json.dumps({"event": "start", "ani": "+966500000001",
                                  "audio": {"encoding": enc, "sample_rate": rate}}))
        state = {"last_audio": time.perf_counter(), "agent_audio_ms": 0.0, "latencies": [], "done": False,
                 "last_agent_text": ""}

        async def receiver():
            async for raw in ws:
                m = json.loads(raw)
                if m["event"] == "media":
                    state["last_audio"] = time.perf_counter()
                    state["agent_audio_ms"] += len(base64.b64decode(m["payload"])) / (rate / 1000 * (1 if ivr else 2))
                elif m["event"] == "transcript":
                    print(f"  {'👤' if m['role'] == 'user' else '🤖'} {m['text']}")
                    if m["role"] == "agent":
                        state["last_agent_text"] = m["text"]
                elif m["event"] == "metric":
                    state["latencies"].append(m["ms"])
                    print(f"     ⏱ caller stopped → first agent audio: {m['ms']:.0f} ms")
                elif m["event"] in ("transfer", "hangup"):
                    print(f"  ☎ {m['event']} {m.get('reason', '')}")
                    state["done"] = True
                elif m["event"] == "clear":
                    print("  ✂ barge-in: agent audio cleared")

        rx = asyncio.create_task(receiver())
        silence = (b"\xff" if ivr else b"\x00\x00") * (frame // (1 if ivr else 2))

        async def stream(audio: bytes) -> None:
            start = time.perf_counter()
            for i in range(0, len(audio), frame):
                await ws.send(json.dumps({"event": "media", "payload": base64.b64encode(audio[i:i + frame]).decode()}))
                await asyncio.sleep(max(0, start + (i // frame + 1) * 0.02 - time.perf_counter()))

        async def wait_agent_done(max_s: float = 25) -> None:
            """Wait until the agent has replied to this turn and then stayed quiet for 1.2 s."""
            t0 = time.perf_counter()
            while time.perf_counter() - t0 < max_s and not state["done"]:
                await stream(silence * 10)       # keep streaming silence like a real line (200 ms)
                replied = state["last_audio"] > t0
                quiet = time.perf_counter() - state["last_audio"]
                asked = state["last_agent_text"].rstrip().endswith(("?", "؟"))
                if replied and (quiet > 1.0 and asked or quiet > 4.0):   # agent asked something, or went quiet
                    return

        await wait_agent_done()                  # greeting
        for clip in clips:
            if state["done"]:
                break
            if isinstance(clip, str):
                print(f"  ⌨ keypad {clip[5:]}")
                for d in clip[5:]:
                    await ws.send(json.dumps({"event": "dtmf", "digit": d}))
                    await stream(silence * 5)          # ~100 ms between key presses
            else:
                await stream(clip)
            await wait_agent_done()
        await ws.send(json.dumps({"event": "stop"}))
        rx.cancel()
    lats = state["latencies"]
    if lats:
        lats_sorted = sorted(lats)
        print(f"\nlatency end-of-turn detected → first agent audio: median {lats_sorted[len(lats)//2]:.0f} ms, "
              f"min {min(lats):.0f}, max {max(lats):.0f}  (+ end-of-turn silence, default 550 ms, for what the "
              f"caller perceives)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("script", type=Path)
    ap.add_argument("--url", default="ws://localhost:8080/ws")
    ap.add_argument("--ivr", action="store_true", help="μ-law 8 kHz like the IVR")
    a = ap.parse_args()
    asyncio.run(main(a.script, a.url, a.ivr))
