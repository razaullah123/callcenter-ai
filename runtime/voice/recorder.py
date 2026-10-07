"""Call recordings: what the caller and the agent said, as one stereo WAV (caller left, agent right), encrypted at rest.

`CallRecorder` collects the audio of one call (both directions, each placed on the call's own timeline) and builds the WAV.
`RecordingStore` writes it encrypted (Fernet under MASTER_KEY — without a key nothing is recorded) to a local folder
(`RECORDINGS_DIR/<project>/<call_id>.wav.enc`), keeps the metadata in `call_recordings` and deletes the file when the agent's
retention (`recording_retention_days`, default 30) is over. Files are only ever read through the console API, which checks
access to the call and writes every play / download to the agent's audit log.

The caller's audio is not recorded while a one-time code is being read out (`auth.otp_sent` and not yet verified): a code
that would still be valid must not sit on disk. The agent says the recording notice (`RECORDING_NOTICE`) before anything else.
"""

import asyncio
import logging
import re
import struct
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from runtime.providers.audio import resample_pcm16

log = logging.getLogger(__name__)

OUT_RATE = 16000
MAX_SECONDS = 2 * 3600            # a longer call stops being recorded (memory)
MAX_GAP_S = 0.06                  # chunks closer than this belong to one run
_SAFE = re.compile(r"[^A-Za-z0-9_.-]")


class CallRecorder:
    """Both sides of one call on a shared clock. Cheap to feed: it only keeps the bytes until `wav()`."""

    def __init__(self) -> None:
        self._t0 = time.monotonic()
        self._runs: dict[int, list[list]] = {0: [], 1: []}      # source -> [[start_s, rate, bytearray], …]
        self._seconds = 0.0
        self.paused = False                                      # the caller's audio is skipped while paused
        self.full = False

    def add(self, source: int, pcm: bytes, rate: int) -> None:
        """source 0 = the caller, 1 = the agent. The agent's audio may arrive faster than real time: it queues after what
        came before, as it plays."""
        if not pcm or self.full or (self.paused and source == 0):
            return
        now = time.monotonic() - self._t0
        runs = self._runs[source]
        last = runs[-1] if runs else None
        start = now
        if last is not None:
            end = last[0] + len(last[2]) / 2 / last[1]
            if last[1] == rate and (now - end <= MAX_GAP_S or (source == 1 and now < end)):     # contiguous, or queued behind
                last[2] += pcm
                self._grow(end + len(pcm) / 2 / rate)
                return
            start = max(now, end)
        runs.append([start, rate, bytearray(pcm)])
        self._grow(start + len(pcm) / 2 / rate)

    def _grow(self, seconds: float) -> None:
        self._seconds = max(self._seconds, seconds)
        if self._seconds >= MAX_SECONDS:
            self.full = True

    @property
    def duration_s(self) -> float:
        return self._seconds

    def has_audio(self) -> bool:
        return any(self._runs[s] for s in (0, 1))

    def wav(self) -> bytes:
        """The stereo WAV at 16 kHz (channel 0 = caller, 1 = agent)."""
        n = int(self._seconds * OUT_RATE) + 1
        out = np.zeros((n, 2), dtype=np.int16)
        for source, runs in self._runs.items():
            for start, rate, buf in runs:
                pcm = bytes(buf[: len(buf) // 2 * 2])
                if rate != OUT_RATE:
                    pcm = resample_pcm16(pcm, rate, OUT_RATE)
                samples = np.frombuffer(pcm, dtype=np.int16)
                i = int(start * OUT_RATE)
                j = min(n, i + len(samples))
                if j > i:
                    out[i:j, source] = samples[: j - i]
        return stereo_wav(out.tobytes(), OUT_RATE)


def stereo_wav(pcm: bytes, rate: int) -> bytes:
    header = b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 2, rate, rate * 4, 4, 16)
    return header + b"data" + struct.pack("<I", len(pcm)) + pcm


class RecordingStore:
    """Encrypted files in a local folder + metadata rows. `cipher` is the platform's (MASTER_KEY)."""

    def __init__(self, directory: Path, cipher, platform) -> None:
        self.dir, self.cipher, self.platform = Path(directory), cipher, platform

    @property
    def available(self) -> bool:
        return bool(getattr(self.cipher, "available", False)) and self.platform is not None

    def _rel(self, workspace: str, call_id: str) -> str:
        return f"{_SAFE.sub('_', workspace)}/{_SAFE.sub('_', call_id)}.wav.enc"

    def _path(self, rel: str) -> Path:
        p = (self.dir / rel).resolve()
        if self.dir.resolve() not in p.parents:                 # never outside the recordings folder
            raise ValueError("recording path outside the recordings folder")
        return p

    async def save(self, *, call_id: str, workspace: str, agent_id: str | None, wav: bytes, duration_s: float,
                   retention_days: int, started_at=None) -> dict:
        rel = self._rel(workspace, call_id)
        row = {"call_id": call_id, "workspace_id": workspace, "agent_id": agent_id, "status": "ok", "path": rel,
               "duration_s": round(duration_s, 1), "started_at": started_at,
               "expires_at": datetime.now(timezone.utc) + timedelta(days=max(1, retention_days))}

        def write() -> int:
            token = self.cipher.encrypt_bytes(wav)
            p = self._path(rel)
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp")
            tmp.write_bytes(token)
            tmp.replace(p)
            return len(token)
        try:
            row["size_bytes"] = await asyncio.get_running_loop().run_in_executor(None, write)
        except Exception as e:                                   # noqa: BLE001
            log.warning("recording of %s not saved: %r", call_id, e)
            row.update(status="failed", path=None, error=repr(e)[:200])
        await self.platform.put_recording(row)
        return row

    async def failed(self, *, call_id: str, workspace: str, agent_id: str | None, error: str, retention_days: int,
                     started_at=None) -> None:
        await self.platform.put_recording({
            "call_id": call_id, "workspace_id": workspace, "agent_id": agent_id, "status": "failed", "error": error[:200],
            "started_at": started_at, "expires_at": datetime.now(timezone.utc) + timedelta(days=max(1, retention_days))})

    async def read(self, row: dict) -> bytes:
        """The decrypted WAV of a recording row (status ok)."""
        def go() -> bytes:
            return self.cipher.decrypt_bytes(self._path(row["path"]).read_bytes())
        return await asyncio.get_running_loop().run_in_executor(None, go)

    async def remove(self, row: dict, status: str) -> None:
        """Delete the file and mark the row (`expired` by the retention job, `deleted` by a person)."""
        if row.get("path"):
            try:
                self._path(row["path"]).unlink(missing_ok=True)
            except Exception as e:                               # noqa: BLE001
                log.warning("recording file %s not removed: %r", row["path"], e)
        await self.platform.end_recording(row["call_id"], status)

    async def purge(self) -> int:
        """Delete every recording whose retention is over; returns how many."""
        n = 0
        for row in await self.platform.due_recordings():
            await self.remove(row, "expired")
            n += 1
        return n


def store_for(rt) -> "RecordingStore | None":
    """The runtime's recording store (made on first use); None without a platform database."""
    store = getattr(rt, "recordings", None)
    if store is None and getattr(rt, "platform", None) is not None and getattr(rt, "secrets", None) is not None:
        store = rt.recordings = RecordingStore(rt.settings.recordings_dir, rt.secrets.cipher, rt.platform)
    return store


async def purge_loop(store: RecordingStore, every_s: int = 3600) -> None:
    while True:
        try:
            n = await store.purge()
            if n:
                log.info("recordings purged: %d", n)
        except Exception as e:                                   # noqa: BLE001
            log.warning("recording purge failed: %r", e)
        await asyncio.sleep(every_s)
