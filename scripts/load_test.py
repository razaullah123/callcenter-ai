"""Concurrent-call load test against the voice server (JSON /ws endpoint, real speech audio).

    # a separate server with fake providers + fixture tools, so no external calls are made:
    PROVIDER_OVERRIDE=fake TOOLS_MODE=mock LOG_CONSOLE=false python -m runtime.server 8090
    python scripts/load_test.py --calls 25 --url ws://localhost:8090/ws

Each call: greeting → 3 caller turns (real speech, streamed in real time) → agent reply. Reports the server's
turn-end → first-audio latency, playback gaps (agent audio arriving late = audible stutter) and the server's
event-loop lag (CPU saturation signal from /health).
"""

import argparse
import asyncio
import base64
import json
import statistics
import sys
import time
from pathlib import Path

import httpx
import websockets

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from runtime.providers.audio import resample_pcm16, wav_to_pcm16  # noqa: E402

RATE, FRAME = 16000, 640          # 20 ms


def load_speech() -> bytes:
    for name in ("logs/smoke_ar.wav", "logs/stream_16k.wav"):
        p = ROOT / name
        if p.exists():
            pcm, rate = wav_to_pcm16(p.read_bytes())
            return resample_pcm16(pcm, rate, RATE)[: RATE * 2 * 3]    # 3 s of real speech
    raise SystemExit("need a speech sample: run scripts/smoke_groq.py once (writes logs/smoke_ar.wav)")


async def one_call(i: int, url: str, speech: bytes, turns: int, stats: dict) -> None:
    silence = bytes(FRAME)
    try:
        async with websockets.connect(url, max_size=None, open_timeout=20) as ws:
            await ws.send(json.dumps({"event": "start", "call_id": f"load-{i}-{int(time.time())}",
                                      "audio": {"encoding": "pcm16", "sample_rate": RATE}}))
            st = {"last": 0.0, "replies": 0, "reply_started": False, "prev": None}

            async def rx():
                async for raw in ws:
                    m = json.loads(raw)
                    if m["event"] == "media":
                        now = time.perf_counter()
                        if st["prev"] is not None and now - st["prev"] > 0.25:   # 20 ms frames, >250 ms late = gap
                            stats["gaps"].append((now - st["prev"]) * 1000)
                        st["prev"] = st["last"] = now
                    elif m["event"] == "metric":
                        stats["latency"].append(m["ms"])
                        st["replies"] += 1

            task = asyncio.create_task(rx())

            async def stream(audio: bytes) -> None:
                t0 = time.perf_counter()
                for n, k in enumerate(range(0, len(audio), FRAME)):
                    await ws.send(json.dumps({"event": "media", "payload": base64.b64encode(audio[k:k + FRAME]).decode()}))
                    await asyncio.sleep(max(0, t0 + (n + 1) * 0.02 - time.perf_counter()))

            async def wait_quiet(min_s=1.2, max_s=20):
                t0 = time.perf_counter()
                while time.perf_counter() - t0 < max_s:
                    await stream(silence * 10)
                    if st["last"] > t0 and time.perf_counter() - st["last"] > min_s:
                        return
                stats["timeouts"] += 1

            st["prev"] = None
            await wait_quiet()
            for _ in range(turns):
                st["prev"] = None
                await stream(speech)
                await wait_quiet()
            stats["completed"] += 1
            task.cancel()
    except Exception as e:
        stats["errors"].append(repr(e)[:120])


async def sample_health(base: str, stats: dict, stop: asyncio.Event) -> None:
    async with httpx.AsyncClient(timeout=5) as c:
        while not stop.is_set():
            try:
                h = (await c.get(f"{base}/health")).json()
                stats["lag"].append(h.get("loop_lag_ms") or 0)
                stats["active"] = max(stats["active"], h.get("active_calls") or 0)
            except Exception:
                pass
            await asyncio.sleep(1)


async def main(calls: int, url: str, turns: int, ramp_s: float) -> None:
    speech = load_speech()
    base = url.replace("ws://", "http://").replace("wss://", "https://").rsplit("/ws", 1)[0]
    stats = {"latency": [], "gaps": [], "lag": [], "errors": [], "completed": 0, "timeouts": 0, "active": 0}
    stop = asyncio.Event()
    health = asyncio.create_task(sample_health(base, stats, stop))
    t0 = time.perf_counter()

    async def delayed(i):
        await asyncio.sleep(i * ramp_s / max(1, calls))
        await one_call(i, url, speech, turns, stats)

    await asyncio.gather(*(delayed(i) for i in range(calls)))
    stop.set()
    await health
    lat, lag = sorted(stats["latency"]), sorted(stats["lag"])
    pct = lambda xs, p: xs[min(len(xs) - 1, int(len(xs) * p))] if xs else None  # noqa: E731
    print(f"\n{calls} concurrent calls · {turns} turns each · {time.perf_counter() - t0:.0f} s")
    print(f"  completed {stats['completed']}/{calls} · peak active {stats['active']} · timeouts {stats['timeouts']} "
          f"· errors {len(stats['errors'])}")
    print(f"  turn end → first audio: p50 {pct(lat, .5)} ms · p95 {pct(lat, .95)} ms · max {lat[-1] if lat else None} "
          f"({len(lat)} replies)")
    print(f"  playback gaps > 250 ms: {len(stats['gaps'])}" +
          (f" (worst {max(stats['gaps']):.0f} ms)" if stats["gaps"] else ""))
    print(f"  event-loop lag: p50 {pct(lag, .5)} ms · p95 {pct(lag, .95)} ms · max {lag[-1] if lag else None} ms")
    if stats["errors"]:
        print("  first errors:", stats["errors"][:3])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--calls", type=int, default=10)
    ap.add_argument("--turns", type=int, default=3)
    ap.add_argument("--ramp", type=float, default=10.0, help="seconds to ramp up all calls")
    ap.add_argument("--url", default="ws://localhost:8090/ws")
    a = ap.parse_args()
    asyncio.run(main(a.calls, a.url, a.turns, a.ramp))
