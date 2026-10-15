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
    return Settings(auth_secret=SECRET, database_url=None, **kw)   # no real DB


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


def test_runtime_never_reads_the_source_database():
    # everything the agent needs is imported into its own database (scripts/clone_reference_data.py)
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "runtime"
    hits = [str(p.relative_to(root)) for p in root.rglob("*.py")
            if p.name != "config.py" and ("source_database_url" in (t := p.read_text(encoding="utf-8"))
                                          or "ai_agent_patient_appointment" in t)]
    assert hits == []


def test_outbound_call_connects_with_its_token_and_reports_back(client):
    """A batch call: the PBX connects the answered call with the token from the dial request."""
    import asyncio

    from runtime.platform import batch
    from runtime.platform.store import MemoryStore
    from runtime.server import app as server_app
    rt = server_app.state["rt"]
    loop = asyncio.new_event_loop()
    store = rt.platform = MemoryStore()

    async def agent_for_call(**kw):
        return None                                           # the fixture's repo agent answers
    rt.agent_for_call = agent_for_call
    rt.batch = batch.BatchDialer(rt)

    async def setup():
        await store.put_batch({"id": "bc_t", "workspace_id": "hmg", "name": "B", "agent_id": "a", "from_number": "+966112000000",
                               "status": "running", "config": {}})
        await store.add_recipients("bc_t", [{"phone": "+966548802968", "name": "Sara", "variables": {"city": "Riyadh"}}])
        (rec,), _ = await store.recipients("bc_t")
        await store.update_recipient(rec["id"], {"status": "in_progress", "dialed_at": batch.datetime.now(batch.timezone.utc)})
        return rec
    rec = loop.run_until_complete(setup())
    token = batch.make_token(rt.settings, "bc_t", rec["id"], 60)
    with pytest.raises(Exception) as e:                        # a forged token is refused like a bad access token
        with client.websocket_connect(f"/ws/voice-pipeline?access_token={_token()}&outbound_token=forged") as ws:
            ws.receive_bytes()
    assert e.value.code == 1008
    with client.websocket_connect(f"/ws/voice-pipeline?phone_number=ignored&access_token={_token()}&outbound_token={token}") as ws:
        ws.receive_bytes()                                     # the greeting
        live = loop.run_until_complete(store.recipient(rec["id"]))
        assert live["call_id"].startswith("ivr-") and live["status"] == "in_progress"
        call = next(iter(rt.calls.values()))
        assert call.session.direction == "outbound" and call.session.agent_number == "+966112000000"
        assert call.session.params == {"name": "Sara", "city": "Riyadh"} and call.session.ani == "+966548802968"
    for _ in range(300):                                       # the socket closed: the recipient is completed
        done = loop.run_until_complete(store.recipient(rec["id"]))
        if done["status"] == "completed":
            break
        import time as _t
        _t.sleep(0.05)
    assert done["status"] == "completed" and done["duration_s"] is not None
    loop.close()


def test_a_draft_phone_test_loads_the_draft(client):
    """An outbound call of a "test via phone" batch asks for the agent's draft, a normal one for the published version."""
    import asyncio

    from runtime.platform import batch
    from runtime.platform.store import MemoryStore
    from runtime.server import app as server_app
    rt = server_app.state["rt"]
    loop = asyncio.new_event_loop()
    store = rt.platform = MemoryStore()
    asked = []

    async def agent_for_call(**kw):
        asked.append(kw)
        return None
    rt.agent_for_call = agent_for_call
    rt.batch = batch.BatchDialer(rt)

    async def setup(bid, cfg):
        await store.put_batch({"id": bid, "workspace_id": "hmg", "name": "B", "agent_id": "a", "from_number": "+966112000000",
                               "status": "running", "config": cfg})
        await store.add_recipients(bid, [{"phone": "+966548802968"}])
        (rec,), _ = await store.recipients(bid)
        return rec["id"]
    for bid, cfg in (("bc_draft", {"draft": True}), ("bc_live", {})):
        rid = loop.run_until_complete(setup(bid, cfg))
        token = batch.make_token(rt.settings, bid, rid, 60)
        with client.websocket_connect(f"/ws/voice-pipeline?access_token={_token()}&outbound_token={token}") as ws:
            ws.receive_bytes()
    assert asked == [{"agent_id": "a", "draft": True}, {"agent_id": "a", "draft": False}]
    loop.close()


def test_a_console_listener_hears_a_real_call_through_the_audio_tap(client):
    """The real VoiceCall: the caller's audio and the agent's replies reach a listener on /api/live/listen."""
    import struct

    from runtime.control import api as control_api
    from runtime.platform.store import MemoryStore
    from runtime.server import app as server_app
    rt = server_app.state["rt"]
    rt.platform, rt.live = MemoryStore(), None
    client.app.include_router(control_api.router)
    with client.websocket_connect(f"/ws/voice-pipeline?phone_number=+966548802968&access_token={_token()}") as ivr:
        ivr.receive_bytes()                                                 # the greeting has started: the call exists now
        (call_id,) = list(rt.calls)
        with client.websocket_connect(f"/api/live/listen?call_id={call_id}") as ear:
            tone = (np.sin(np.arange(8000) / 3) * 16000).astype(np.int16).tobytes()       # 1 s of "speech" @ 8 kHz, then 0.5 s silence
            for chunk in [tone[i:i + 320] for i in range(0, len(tone), 320)] + [bytes(320)] * 25:
                ivr.send_bytes(chunk)
            sources = set()
            for _ in range(3000):                                           # the agent's reply (and the handoff sentence) follow
                frame = ear.receive_bytes()
                source, rate = struct.unpack(">BH", frame[:3])
                assert rate in (8000, 24000) and len(frame) > 3 and (len(frame) - 3) % 2 == 0
                sources.add(source)
                if sources == {0, 1}:
                    break
            assert sources == {0, 1}                                         # both sides of the conversation
