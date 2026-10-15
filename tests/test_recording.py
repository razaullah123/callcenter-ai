"""Call recordings: the recorder's timeline, encrypted storage and retention, the console API and the call's wiring."""

import struct
import wave
from datetime import datetime, timedelta, timezone
from io import BytesIO
from types import SimpleNamespace

import numpy as np
import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.control import api as control_api
from runtime.platform.secrets import Cipher
from runtime.platform.store import MemoryStore
from runtime.server import app as server_app
from runtime.voice import recorder as rec_mod
from runtime.voice.call import VoiceCall
from runtime.voice.recorder import CallRecorder, RecordingStore


def tone(ms: int, rate: int = 16000, value: int = 1000) -> bytes:
    return np.full(rate * ms // 1000, value, dtype=np.int16).tobytes()


def channels(wav: bytes) -> tuple[np.ndarray, int]:
    with wave.open(BytesIO(wav)) as w:
        assert w.getnchannels() == 2 and w.getsampwidth() == 2
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).reshape(-1, 2)
        return data, w.getframerate()


# ---------------------------------------------------------------- the recorder

def test_caller_left_agent_right_on_one_timeline(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(rec_mod.time, "monotonic", lambda: clock[0])
    r = CallRecorder()
    r.add(0, tone(200, value=1000), 16000)               # caller at 0.0 s
    clock[0] += 0.5
    r.add(1, tone(300, 8000, value=-2000), 8000)         # agent at 0.5 s, line rate 8 kHz
    data, rate = channels(r.wav())
    assert rate == 16000 and abs(r.duration_s - 0.8) < 0.01
    assert (data[: 3000, 0] == 1000).all() and (data[: 3000, 1] == 0).all()          # caller only
    assert (data[8100:12000, 1] < 0).all() and (data[8100:12000, 0] == 0).all()      # agent only, resampled to 16 kHz


def test_agent_audio_sent_faster_than_real_time_queues_behind_itself(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(rec_mod.time, "monotonic", lambda: clock[0])
    r = CallRecorder()
    for _ in range(5):
        r.add(1, tone(200, value=500), 16000)            # a full second of speech sent in one burst
    assert abs(r.duration_s - 1.0) < 0.01
    data, _ = channels(r.wav())
    assert (data[:15900, 1] == 500).all()                # no overlap, no loss


def test_the_callers_audio_is_skipped_while_paused():
    r = CallRecorder()
    r.paused = True
    r.add(0, tone(200), 16000)
    assert not r.has_audio()
    r.add(1, tone(200), 16000)                           # the agent is still recorded
    assert r.has_audio()
    r.paused = False
    r.add(0, tone(200), 16000)
    data, _ = channels(r.wav())
    assert (data[:, 0] != 0).any()


def test_a_very_long_call_stops_being_recorded(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(rec_mod.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(rec_mod, "MAX_SECONDS", 1.0)
    r = CallRecorder()
    r.add(0, tone(1200), 16000)
    assert r.full
    r.add(0, tone(1000), 16000)
    assert r.duration_s < 1.5


# ---------------------------------------------------------------- the store

@pytest.fixture
def stores(tmp_path):
    platform = MemoryStore()
    cipher = Cipher(Fernet.generate_key().decode())
    return platform, RecordingStore(tmp_path / "recs", cipher, platform), tmp_path


async def test_saved_encrypted_read_back_and_purged(stores):
    platform, recs, tmp = stores
    wav = rec_mod.stereo_wav(tone(300) * 2, 16000)
    row = await recs.save(call_id="c1", workspace="hmg", agent_id="a1", wav=wav, duration_s=0.3, retention_days=30)
    assert row["status"] == "ok" and row["path"] == "hmg/c1.wav.enc"
    on_disk = (tmp / "recs" / row["path"]).read_bytes()
    assert b"RIFF" not in on_disk and wav not in on_disk                      # encrypted at rest
    assert await recs.read(await platform.recording("c1")) == wav
    assert timedelta(days=29) < row["expires_at"] - datetime.now(timezone.utc) < timedelta(days=31)
    assert await recs.purge() == 0                                            # not yet due
    stored = await platform.recording("c1")
    stored["expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)
    await platform.put_recording(stored)
    assert await recs.purge() == 1
    assert (await platform.recording("c1"))["status"] == "expired" and not (tmp / "recs" / row["path"]).exists()


async def test_a_wrong_key_cannot_read_and_paths_stay_inside(stores):
    platform, recs, tmp = stores
    await recs.save(call_id="c2", workspace="hmg", agent_id=None, wav=b"RIFFxxxx", duration_s=1, retention_days=1)
    other = RecordingStore(tmp / "recs", Cipher(Fernet.generate_key().decode()), platform)
    with pytest.raises(Exception):
        await other.read(await platform.recording("c2"))
    with pytest.raises(ValueError):
        recs._path("../../outside.enc")
    weird = await recs.save(call_id="../../x", workspace="../w", agent_id=None, wav=b"RIFFxxxx", duration_s=1, retention_days=1)
    assert (tmp / "recs" / weird["path"]).resolve().is_relative_to((tmp / "recs").resolve())


def test_no_master_key_means_nothing_is_recorded():
    platform = MemoryStore()
    assert not RecordingStore("x", Cipher(None), platform).available
    assert RecordingStore("x", Cipher(Fernet.generate_key().decode()), platform).available


# ---------------------------------------------------------------- the call

def fake_call(store, record=True, auth=None):
    ev = SimpleNamespace(emit=lambda *a, **k: events.append((a, k)))
    events: list = []
    rt = SimpleNamespace(settings=SimpleNamespace(recordings_dir=store.dir), platform=store.platform,
                         secrets=SimpleNamespace(cipher=store.cipher), recordings=store)
    loaded = SimpleNamespace(agent_id="a1", settings=SimpleNamespace(record_calls=record, recording_retention_days=7))
    session = SimpleNamespace(call_id="call-1", auth=auth or SimpleNamespace(otp_sent=False, verified=False))
    c = SimpleNamespace(rt=rt, ev=ev, loaded=loaded, session=session, recorder=None, aec=None, _listeners=set(), _stopped=False,
                        _out_fmt=SimpleNamespace(encoding="pcm16", sample_rate=16000), in_fmt=SimpleNamespace(encoding="pcm16", sample_rate=16000),
                        _started_at=datetime.now(timezone.utc), events=events)

    async def raw(data):
        pass
    c._raw_send_audio = raw
    return c


async def test_a_recorded_call_is_saved_when_it_ends(stores):
    platform, recs, _ = stores
    c = fake_call(recs)
    c.recorder = VoiceCall._start_recorder(c, c.loaded.settings)
    assert c.recorder is not None
    c.recorder.add(0, tone(300), 16000)
    await VoiceCall._send_and_reference(c, tone(200))
    await VoiceCall._save_recording(c)
    row = await platform.recording("call-1")
    assert row["status"] == "ok" and row["agent_id"] == "a1" and row["duration_s"] > 0.2
    assert timedelta(days=6) < row["expires_at"] - datetime.now(timezone.utc) < timedelta(days=8)       # the agent's retention
    assert c.recorder is None


async def test_nothing_is_recorded_when_off_or_when_it_cannot_be_stored(stores):
    platform, recs, _ = stores
    off = fake_call(recs, record=False)
    assert VoiceCall._start_recorder(off, off.loaded.settings) is None and not off.events
    nokey = fake_call(RecordingStore(recs.dir, Cipher(None), platform))
    assert VoiceCall._start_recorder(nokey, nokey.loaded.settings) is None
    assert any("MASTER_KEY" in str(e) for e in nokey.events)                    # said in the logs, never silently
    await VoiceCall._save_recording(off)
    assert await platform.recording("call-1") is None


# ---------------------------------------------------------------- the console API

@pytest.fixture
def api(monkeypatch, stores):
    platform, recs, _ = stores
    rt = SimpleNamespace(platform=platform, settings=SimpleNamespace(console_token=None, recordings_dir=recs.dir), live=None,
                         calls={}, secrets=SimpleNamespace(cipher=recs.cipher), recordings=recs)
    monkeypatch.setitem(server_app.state, "rt", rt)

    async def get_call(call_id, scope):
        return {"call": {"call_id": call_id, "agent_id": "a1"}} if call_id.startswith("call") else None

    async def scope(*a, **k):
        return (["a1"], False)
    monkeypatch.setattr(control_api.store, "get_call", get_call)
    monkeypatch.setattr(control_api, "project_scope", scope)
    app = FastAPI()
    app.include_router(control_api.router)
    with TestClient(app) as c:
        yield c, platform, recs


def test_play_download_and_delete_through_the_api_are_audited(api):
    import asyncio
    c, platform, recs = api
    loop = asyncio.new_event_loop()
    wav = rec_mod.stereo_wav(tone(300) * 2, 16000)
    loop.run_until_complete(recs.save(call_id="call-1", workspace="hmg", agent_id="a1", wav=wav, duration_s=0.3, retention_days=30))
    r = c.get("/api/calls/call-1/recording")
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav" and r.content == wav
    assert r.headers["cache-control"] == "no-store" and "inline" in r.headers["content-disposition"]
    d = c.get("/api/calls/call-1/recording?download=true")
    assert "attachment" in d.headers["content-disposition"]
    assert c.get("/api/calls/other/recording").status_code == 404                 # a call the caller can't see
    log = [a["action"] for a in loop.run_until_complete(platform.audit_log("a1"))]
    assert log.count("call.recording.play") == 1 and log.count("call.recording.download") == 1
    assert c.delete("/api/calls/call-1/recording").json()["deleted"] is True
    gone = c.get("/api/calls/call-1/recording")
    assert gone.status_code == 404 and "deleted" in gone.json()["detail"]
    assert "call.recording.delete" in [a["action"] for a in loop.run_until_complete(platform.audit_log("a1"))]
    loop.close()


def test_a_call_that_was_not_recorded_says_so(api):
    c, _, _ = api
    r = c.get("/api/calls/call-9/recording")
    assert r.status_code == 404 and "not recorded" in r.json()["detail"]


def test_the_recording_knobs_and_notice_exist():
    from runtime.harness.prompts import Phrases
    from runtime.platform.bundle import KNOBS, knob_errors
    assert KNOBS["record_calls"] is bool and KNOBS["recording_retention_days"] is int
    assert knob_errors({"record_calls": True, "recording_retention_days": 30}) == []
    assert "record" in Phrases().RECORDING_NOTICE["en"] and Phrases().RECORDING_NOTICE["ar"]
