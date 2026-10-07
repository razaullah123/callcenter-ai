"""Hamsa parity (live monitoring): listening in on a running call from the console — the tap in the call and the socket."""

import asyncio
import json
import struct
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.control import api as control_api
from runtime.platform.store import MemoryStore
from runtime.providers.audio import pcm16_to_mulaw
from runtime.server import app as server_app
from runtime.voice.call import VoiceCall
from runtime.voice.player import AudioFormat


def fake_call(**kw):
    c = SimpleNamespace(_stopped=False, _listeners=set(), aec=None, session=SimpleNamespace(agent_id="a1"),
                        _out_fmt=AudioFormat("pcm16", 24000), in_fmt=AudioFormat("pcm16", 16000), sent=[], **kw)
    c._tap = lambda *a: VoiceCall._tap(c, *a)
    c.add_listener = lambda: VoiceCall.add_listener(c)
    c.remove_listener = lambda q: VoiceCall.remove_listener(c, q)

    async def raw(data):
        c.sent.append(data)
    c._raw_send_audio = raw
    c.MAX_LISTENERS = VoiceCall.MAX_LISTENERS
    return c


# ---------------------------------------------------------------- the tap in the call

def test_listeners_are_limited_and_never_block_the_call():
    c = fake_call()
    qs = [c.add_listener() for _ in range(VoiceCall.MAX_LISTENERS)]
    assert all(qs) and c.add_listener() is None                              # a fourth is refused
    c.remove_listener(qs[0])
    assert c.add_listener() is not None
    full = next(iter(c._listeners))
    for _ in range(full.maxsize):
        full.put_nowait((0, 8000, b"x"))
    c._tap(0, b"\x00\x01", 8000)                                              # a full queue drops audio, never raises or waits
    assert full.qsize() == full.maxsize
    stopped = fake_call()
    stopped._stopped = True
    assert stopped.add_listener() is None


async def test_the_agents_audio_is_tapped_in_its_own_format():
    c = fake_call()
    q = c.add_listener()
    await VoiceCall._send_and_reference(c, b"\x01\x02\x03\x04")
    assert c.sent == [b"\x01\x02\x03\x04"] and q.get_nowait() == (1, 24000, b"\x01\x02\x03\x04")
    ulaw = fake_call()
    ulaw._out_fmt = AudioFormat("mulaw", 8000)
    q2 = ulaw.add_listener()
    await VoiceCall._send_and_reference(ulaw, pcm16_to_mulaw(b"\x10\x00" * 80))
    source, rate, pcm = q2.get_nowait()
    assert (source, rate, len(pcm)) == (1, 8000, 160)                         # decoded to PCM16 for the console
    none = fake_call()
    await VoiceCall._send_and_reference(none, b"zz")                          # nobody listens: nothing is decoded or queued
    assert none.sent == [b"zz"]


# ---------------------------------------------------------------- the socket

@pytest.fixture
def ws_client(monkeypatch):
    store = MemoryStore()
    rt = SimpleNamespace(platform=store, settings=SimpleNamespace(console_token=None), live=None, calls={})
    monkeypatch.setitem(server_app.state, "rt", rt)
    app = FastAPI()
    app.include_router(control_api.router)
    with TestClient(app) as c:
        yield c, rt, store


def test_a_listener_receives_both_sides_and_the_call_end(ws_client):
    c, rt, store = ws_client
    call = fake_call()
    rt.calls["live-1"] = call
    loop = asyncio.new_event_loop()
    with c.websocket_connect("/api/live/listen?call_id=live-1") as ws:
        q = next(iter(call._listeners))
        q.put_nowait((0, 8000, b"\x01\x00\x02\x00"))
        q.put_nowait((1, 24000, b"\x03\x00"))
        first, second = ws.receive_bytes(), ws.receive_bytes()
        assert struct.unpack(">BH", first[:3]) == (0, 8000) and first[3:] == b"\x01\x00\x02\x00"
        assert struct.unpack(">BH", second[:3]) == (1, 24000) and second[3:] == b"\x03\x00"
        call._stopped = True
        assert json.loads(ws.receive_text()) == {"type": "ended"}
    assert call._listeners == set()                                           # the queue is released
    audit = loop.run_until_complete(store.audit_log("a1"))
    assert [a["action"] for a in audit] == ["call.listen"] and audit[0]["detail"] == {"call_id": "live-1"}
    loop.close()


def test_a_closed_socket_stops_listening(ws_client):
    c, rt, store = ws_client
    call = fake_call()
    rt.calls["live-1"] = call
    with c.websocket_connect("/api/live/listen?call_id=live-1") as ws:
        assert len(call._listeners) == 1
        ws.close()
    for _ in range(50):
        if not call._listeners:
            break
        import time
        time.sleep(0.05)
    assert call._listeners == set()


def test_refusals_are_explained(ws_client):
    c, rt, store = ws_client
    with c.websocket_connect("/api/live/listen?call_id=gone") as ws:
        msg = json.loads(ws.receive_text())
        assert msg["type"] == "error" and "not running here" in msg["message"]
    call = fake_call()
    rt.calls["busy"] = call
    for _ in range(VoiceCall.MAX_LISTENERS):
        call.add_listener()
    with c.websocket_connect("/api/live/listen?call_id=busy") as ws:
        assert "Too many" in json.loads(ws.receive_text())["message"]


def test_a_stranger_cannot_listen(monkeypatch):
    store = MemoryStore()
    rt = SimpleNamespace(platform=store, settings=SimpleNamespace(console_token=SimpleNamespace(get_secret_value=lambda: "secret")),
                         live=None, calls={"live-1": fake_call()})
    monkeypatch.setitem(server_app.state, "rt", rt)
    app = FastAPI()
    app.include_router(control_api.router)
    with TestClient(app) as c:
        with pytest.raises(Exception):
            with c.websocket_connect("/api/live/listen?call_id=live-1") as ws:       # no token
                ws.receive_text()
        with pytest.raises(Exception):
            with c.websocket_connect("/api/live/listen?call_id=live-1&token=wrong") as ws:
                ws.receive_text()
        with c.websocket_connect("/api/live/listen?call_id=live-1&token=secret") as ws:
            assert next(iter(rt.calls["live-1"]._listeners)) is not None
