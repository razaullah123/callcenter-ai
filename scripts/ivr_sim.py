"""Simulated IVR / phone line for /ws/voice-pipeline — the exact production wire protocol.

    python -m runtime.server
    python scripts/ivr_sim.py scripts/flows/voice_ar.txt [--phone 0551234567] [--echo 0.3] [--token JWT]

Sends raw PCM16 8 kHz binary frames (20 ms, real time), receives WAV pieces and {"action":"transfer"}.
--echo G feeds the agent's own audio back into the uplink at gain G after 60 ms (speakerphone / line echo)
to exercise echo cancellation and the barge-in guards. The conversation is printed from the server's event log.
"""

import argparse
import asyncio
import io
import json
import re
import sys
import time
import wave
from pathlib import Path

import numpy as np
import websockets

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from runtime.providers import create  # noqa: E402
from runtime.providers.audio import resample_pcm16  # noqa: E402

AR = re.compile(r"[؀-ۿ]")
RATE, FRAME = 8000, 320            # 20 ms @ 8 kHz, 16-bit


async def synth(tts, text: str) -> bytes:
    lang = "ar" if AR.search(text) else "en"
    pcm, rate = b"", 24000
    async for c in tts.synthesize(text, language=lang, voice=None if lang == "ar" else "troy"):
        pcm, rate = pcm + c.data, c.sample_rate
    return resample_pcm16(pcm, rate, RATE)


async def main(script: Path, url: str, phone: str, token: str, echo_gain: float) -> None:
    lines = [l.strip() for l in script.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]
    tts = create("tts", "groq")
    print(f"synthesizing {len(lines)} caller lines…")
    clips = [await synth(tts, l) for l in lines]
    echo_buf = bytearray()            # agent audio waiting to leak back into the uplink
    state = {"last_audio": 0.0, "done": False, "transfer": None, "audio_s": 0.0, "pieces": 0}

    async with websockets.connect(f"{url}?phone_number={phone}&access_token={token}", max_size=None) as ws:
        t_connect = time.perf_counter()

        async def receiver():
            async for msg in ws:
                if isinstance(msg, bytes):
                    with wave.open(io.BytesIO(msg)) as w:
                        rate, pcm = w.getframerate(), w.readframes(w.getnframes())
                    if state["pieces"] == 0:
                        print(f"  first WAV piece after {(time.perf_counter() - t_connect) * 1000:.0f} ms "
                              f"({rate} Hz, {len(pcm) / 2 / rate * 1000:.0f} ms)")
                    state["pieces"] += 1
                    state["audio_s"] += len(pcm) / 2 / rate
                    state["last_audio"] = time.perf_counter()
                    if echo_gain:
                        echo_buf.extend(resample_pcm16(pcm, rate, RATE))
                else:
                    data = json.loads(msg)
                    print(f"  ⇦ {data}")
                    if data.get("action") == "transfer":
                        state["transfer"], state["done"] = data, True
            state["done"] = True

        rx = asyncio.create_task(receiver())
        delay = bytes(int(RATE * 0.06) * 2)          # 60 ms echo path
        echo_buf.extend(delay)

        async def stream(audio: bytes) -> None:
            start = time.perf_counter()
            for n, i in enumerate(range(0, len(audio), FRAME)):
                frame = np.frombuffer(audio[i:i + FRAME].ljust(FRAME, b"\x00"), dtype=np.int16).astype(np.int32)
                if echo_gain and len(echo_buf) >= FRAME:
                    leak = np.frombuffer(bytes(echo_buf[:FRAME]), dtype=np.int16).astype(np.int32)
                    del echo_buf[:FRAME]
                    frame = frame + (leak * echo_gain).astype(np.int32)
                await ws.send(np.clip(frame, -32768, 32767).astype(np.int16).tobytes())
                await asyncio.sleep(max(0, start + (n + 1) * 0.02 - time.perf_counter()))

        async def wait_agent(max_s: float = 25) -> None:
            t0 = time.perf_counter()
            while not state["done"] and time.perf_counter() - t0 < max_s:
                await stream(bytes(FRAME * 10))      # line silence (+ echo) while the agent talks
                if state["last_audio"] > t0 and time.perf_counter() - state["last_audio"] > 1.5:
                    return

        await wait_agent()
        for clip in clips:
            if state["done"]:
                break
            await stream(clip)
            await wait_agent()
        rx.cancel()
    print(f"\nreceived {state['pieces']} WAV pieces, {state['audio_s']:.1f} s of agent audio, "
          f"transfer={state['transfer']}")
    print_conversation()


def print_conversation() -> None:
    """The IVR protocol carries no transcripts — read the conversation from the server's event log."""
    import glob
    rows = [json.loads(l) for l in open(sorted(glob.glob(str(ROOT / "logs" / "events-*.jsonl")))[-1],
                                         encoding="utf-8")]
    ivr_calls = [r["call_id"] for r in rows if r["type"] == "call.start" and str(r.get("call_id", "")).startswith("ivr-")]
    if not ivr_calls:
        return
    cid = ivr_calls[-1]
    print(f"\nconversation ({cid}):")
    for r in rows:
        if r.get("call_id") != cid:
            continue
        d = r["data"]
        if r["type"] == "stt.result":
            print(f"  👤 {d.get('text')}   [stt {r.get('latency_ms')} ms{' spec' if d.get('speculative') else ''}]")
        elif r["type"] == "agent.say":
            print(f"  🤖 {d.get('text')}")
        elif r["type"] == "tts.first_byte" and d.get("metric"):
            print(f"       ⏱ turn end → first audio {r.get('latency_ms'):.0f} ms")
        elif r["type"] in ("interrupt", "handoff") or (r["type"] == "policy.block" and "barge" in str(d.get("reason"))):
            print(f"  ⚑ {r['type']} {d}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("script", type=Path)
    ap.add_argument("--url", default="ws://localhost:8080/ws/voice-pipeline")
    ap.add_argument("--phone", default="0551234567")
    ap.add_argument("--token", default="dev")
    ap.add_argument("--echo", type=float, default=0.0, help="echo gain, e.g. 0.3")
    a = ap.parse_args()
    asyncio.run(main(a.script, a.url, a.phone, a.token, a.echo))
