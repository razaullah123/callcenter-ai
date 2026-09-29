"""IVR endpoint /ws/voice-pipeline — wire protocol compatibility (offline: fake TTS / STT / VAD, scripted LLM)."""

import io
import json
import time
import wave

import jwt
import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.config import Settings
from runtime.data.reference import Project, ReferenceData, projects_by_extension, set_reference
from runtime.events import EventBus
from runtime.providers import AudioChunk, Transcript
from runtime.server import ivr
from runtime.skills import FileSkillSet
from runtime.tools import MockMCP
from runtime.tools.factory import build_tooling

from .test_harness import ScriptedLLM

SECRET = "test-secret"


def _project(ref_id, name, ext):
    return Project(ref_id, name, None, "Riyadh", "الرياض", 24.7, 46.6, (), ext)


REF = ReferenceData(projects=[_project(12, "Olaya Hospital", "8880"), _project(15, "Arryan Hospital", "8883"),
                              _project(99, "Small Center", "777")])


def test_extension_routing_4_then_3_digits():
    assert [p.reference_id for p in projects_by_extension(REF, "8880")] == [12]
    assert [p.reference_id for p in projects_by_extension(REF, "88831234")] == [15]   # trailing PBX sub-code
    assert [p.reference_id for p in projects_by_extension(REF, "7771")] == [99]       # 3-digit base extension
    assert projects_by_extension(REF, "1234") == []


def test_phone_repair():
    assert ivr.repair_phone(" 966548802968") == "+966548802968"   # unencoded "+" arrives as a space
    assert ivr.repair_phone("0548802968") == "0548802968"


def _settings(**kw):
    return Settings(auth_secret=SECRET, auth_database_url=None, source_database_url=None, **kw)   # no real DB


async def test_token_verification():
    s = _settings()
    ok = jwt.encode({"sub": "ivr", "exp": int(time.time()) + 60}, SECRET, algorithm="HS256")
    expired = jwt.encode({"sub": "ivr", "exp": int(time.time()) - 60}, SECRET, algorithm="HS256")
    forged = jwt.encode({"sub": "ivr", "exp": int(time.time()) + 60}, "other", algorithm="HS256")
    assert (await ivr.verify_access_token(ok, s))["sub"] == "ivr"
    assert await ivr.verify_access_token(expired, s) is None
    assert await ivr.verify_access_token(forged, s) is None


# ---------------------------------------------------------------- end-to-end over the socket


class FakeTTS:
    settings = None

    async def synthesize(self, text, *, language="ar", voice=None, encoding="pcm16", sample_rate=None):
        yield AudioChunk(b"\x01\x00" * ((sample_rate or 24000) // 5), sample_rate or 24000, "pcm16")   # 200 ms


class FakeSTT:
    async def transcribe(self, audio, *, language=None, prompt=None):
        return Transcript("ابي اكلم موظف", "ar", audio.duration_ms)


class LoudVAD:
    def __init__(self, sample_rate=16000, **_):
        self.window = 512 * sample_rate // 16000

    def reset(self):
        pass

    def __call__(self, window):
        return 0.9 if np.abs(window).mean() > 0.05 else 0.05


class FakeRuntime:
    def __init__(self, settings, bus, executor, skills):
        from runtime.control.config_store import ProviderSet
        self.settings, self.bus, self.executor, self.skills = settings, bus, executor, skills
        self.providers = ProviderSet(0, {}, ScriptedLLM(), FakeSTT(), FakeTTS())
        self.llm, self.stt, self.tts = self.providers.llm, self.providers.stt, self.providers.tts
        self.calls = {}


@pytest.fixture
def client(monkeypatch):
    import asyncio

    from runtime.server import app as server_app
    from runtime.voice import turn
    monkeypatch.setattr(turn, "SileroVAD", LoudVAD)
    set_reference(REF)
    loop = asyncio.new_event_loop()
    executor = loop.run_until_complete(build_tooling(MockMCP()))
    bus = EventBus()
    settings = _settings(ivr_aec=False, ivr_chunk_ms=100, ivr_transfer_destination="9999",
                         voice_end_silence_ms=300)
    rt = FakeRuntime(settings, bus, executor, FileSkillSet(executor.catalog))
    monkeypatch.setitem(server_app.state, "rt", rt)
    monkeypatch.setitem(server_app.state, "phrases", None)
    app = FastAPI()
    app.include_router(ivr.router)
    with TestClient(app) as c:
        yield c
    loop.close()


def _token():
    return jwt.encode({"sub": "ivr", "jti": "j1", "exp": int(time.time()) + 60}, SECRET, algorithm="HS256")


def test_rejects_bad_token(client):
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect) as e:
        with client.websocket_connect("/ws/voice-pipeline?phone_number=0548802968&access_token=nope") as ws:
            ws.receive_bytes()
    assert e.value.code == 1008


def test_ivr_protocol_greeting_audio_ping_and_transfer(client):
    url = f"/ws/voice-pipeline?phone_number=+966548802968&access_token={_token()}"
    with client.websocket_connect(url) as ws:
        first = ws.receive_bytes()                                  # greeting: complete WAV files
        with wave.open(io.BytesIO(first)) as w:
            assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (8000, 1, 2)
            assert w.getnframes() == 800                            # 100 ms pieces @ 8 kHz
        ws.send_text(json.dumps({"type": "ping"}))                  # keep-alive, no reply
        tone = (np.sin(np.arange(8000) / 3) * 16000).astype(np.int16).tobytes()   # 1 s "speech" @ 8 kHz
        silence = bytes(8000)                                                     # 0.5 s
        for chunk in (tone[i:i + 320] for i in range(0, len(tone), 320)):
            ws.send_bytes(chunk)                                    # raw PCM16 frames, no header
        for i in range(0, len(silence), 320):
            ws.send_bytes(silence[i:i + 320])
        transfer = None
        for _ in range(2000):                                       # skip audio until the transfer command
            msg = ws.receive()
            if msg.get("text"):
                transfer = json.loads(msg["text"])
                break
        assert transfer == {"action": "transfer", "destination": "9999"}
