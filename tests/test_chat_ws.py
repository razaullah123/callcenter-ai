"""Text test channel (/ws/chat): the Studio's Chat test mode."""

import asyncio
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.config import get_settings
from runtime.control.config_store import ProviderSet
from runtime.events import EventBus
from runtime.platform.loader import LoadedAgent
from runtime.skills import FileSkillSet
from runtime.tools import MockMCP
from runtime.tools.factory import build_tooling

from .test_harness import ScriptedLLM


class _Rt:
    def __init__(self, agent, bus):
        self.agent, self.bus, self.calls, self.asked = agent, bus, {}, []

    async def agent_for_call(self, *, agent_id=None, number=None, draft=False):
        self.asked.append((agent_id, draft))
        return self.agent


def test_chat_test_channel(monkeypatch):
    from runtime.server import app as server_app
    from runtime.server import chat
    loop = asyncio.new_event_loop()
    executor = loop.run_until_complete(build_tooling(MockMCP()))
    bus = EventBus()
    agent = LoadedAgent.from_parts(settings=get_settings(), providers=ProviderSet(0, {}, ScriptedLLM(), None, None),
                                   skills=FileSkillSet(executor.catalog), executor=executor)
    rt = _Rt(agent, bus)
    monkeypatch.setitem(server_app.state, "rt", rt)
    app = FastAPI()
    app.include_router(chat.router)
    with TestClient(app) as c, c.websocket_connect("/ws/chat") as ws:
        ws.send_text(json.dumps({"event": "start", "agent": "hmg-care", "draft": True, "language": "en"}))
        ready = json.loads(ws.receive_text())
        assert ready["event"] == "ready" and ready["call_id"].startswith("chat-")
        assert rt.asked == [("hmg-care", True)] and ready["call_id"] in rt.calls
        greeting = json.loads(ws.receive_text())
        assert greeting["event"] == "transcript" and greeting["role"] == "agent"
        ws.send_text(json.dumps({"event": "text", "text": "Good morning"}))
        reply = json.loads(ws.receive_text())
        assert reply["role"] == "agent" and "morning" in reply["text"].lower()   # greeted back in kind
        ws.send_text(json.dumps({"event": "stop"}))
    assert rt.calls == {}
    loop.close()
