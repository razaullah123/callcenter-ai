"""Text test channel for the Agent Studio: type to the agent instead of speaking (same agent, flow, tools and events —
only the audio is skipped), so flows can be checked quickly. Call ids start with "chat-".

    ws://<host>/ws/chat
  client → agent   {"event": "start", "agent": "<id>", "draft": true, "language": "ar"|"en"}
                   {"event": "text", "text": "..."}
                   {"event": "stop"}
  agent → client   {"event": "ready", "call_id": "..."}
                   {"event": "transcript", "role": "agent", "text": "..."}
                   {"event": "transfer", "reason": "..."} · {"event": "hangup"} · {"event": "error", "message": "..."}
"""

import asyncio
import json
import logging
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from runtime.events import EventType, Level
from runtime.harness.engine import Agent
from runtime.harness.handoff import carry_over
from runtime.harness.variables import build_custom, declared_variables
from runtime.harness.session import Session

from . import CLOSED_ERRORS

log = logging.getLogger("runtime.server")
router = APIRouter()


class _TextOutput:
    def __init__(self, send) -> None:
        self.send = send
        self.ended = asyncio.Event()

    async def say(self, text: str, *, language: str, interruptible: bool = True) -> None:
        await self.send({"event": "transcript", "role": "agent", "text": text})

    async def transfer(self, reason: str, **options) -> None:
        await self.send({"event": "transfer", "reason": reason, **options})
        self.ended.set()

    async def hangup(self) -> None:
        await self.send({"event": "hangup"})
        self.ended.set()


class _ChatCall:
    """What the console's "End call" needs from a running call."""

    def __init__(self, session: Session, output: _TextOutput) -> None:
        self.session, self.output = session, output

    async def end_from_console(self) -> None:
        self.output.ended.set()


@router.websocket("/ws/chat")
async def chat(ws: WebSocket) -> None:
    from runtime.server.app import state
    await ws.accept()
    rt = state["rt"]
    lock = asyncio.Lock()

    async def send(msg: dict) -> None:
        async with lock:
            try:
                await ws.send_text(json.dumps(msg, ensure_ascii=False))
            except CLOSED_ERRORS:
                pass

    call_id = f"chat-{uuid.uuid4().hex[:12]}"
    ev = rt.bus.bind(call_id=call_id)
    try:
        first = json.loads(await ws.receive_text())
        if first.get("event") != "start":
            await ws.close(code=1008, reason="first message must be start")
            return
        try:
            loaded = await rt.agent_for_call(agent_id=first.get("agent") or None, draft=bool(first.get("draft")))
        except LookupError as e:
            await send({"event": "error", "message": str(e)})
            await ws.close(code=1008, reason="unknown agent")
            return
        try:                                   # custom variables: the agent's defaults + the start's `params`
            session = Session(call_id=call_id, agent_name=loaded.name, agent_id=loaded.agent_id,
                              params=dict(first.get("params") or {}),
                              custom=build_custom(declared_variables(loaded.bundle), first.get("params")))
        except (ValueError, TypeError) as e:
            await send({"event": "error", "message": str(e)})
            await ws.close(code=1008, reason="bad params")
            return
        if first.get("language") in ("ar", "en"):
            session.language.language = first["language"]
        out = _TextOutput(send)
        def make_agent(target, sess: Session) -> Agent:
            a = Agent(sess, target.executor, target.llm, target.skills, out, rt.bus.bind(),
                      filler_after_s=target.settings.voice_filler_after_s, settings=target.settings)
            a.config_version = target.version
            a.agent_ref = {"agent_id": target.agent_id, "release_id": target.release_id}
            a.transfer_agent = switch_agent
            return a

        async def switch_agent(req) -> bool:
            """A flow's \"transfer agent\" node: another agent of this project takes over the chat."""
            nonlocal agent, session, loaded
            try:
                target = await rt.agent_for_call(agent_id=req.agent_id)
            except Exception as e:                                              # noqa: BLE001
                ev.emit(EventType.ERROR, level=Level.WARNING, during="agent_transfer", agent=req.agent_id, error=repr(e)[:160])
                return False
            previous = loaded.agent_id
            session, loaded = carry_over(session, target.name, req, agent_id=target.agent_id,
                                         declared=declared_variables(target.bundle)), target
            agent = make_agent(target, session)
            rt.calls[call_id] = _ChatCall(session, out)
            ev.emit(EventType.AGENT_TRANSFER, agent_id=target.agent_id, release_id=target.release_id,
                    config_version=target.version, previous=previous, history=req.history, variables=req.variables)
            await send({"event": "agent_transfer", "agent_id": target.agent_id, "name": target.name})
            await agent.start(resumed=True, greet=not req.history)
            return True

        agent = make_agent(loaded, session)
        rt.calls[call_id] = _ChatCall(session, out)
        await send({"event": "ready", "call_id": call_id})
        await agent.start()
        while not out.ended.is_set():
            receive = asyncio.ensure_future(ws.receive_text())
            ended = asyncio.ensure_future(out.ended.wait())
            done, _ = await asyncio.wait({receive, ended}, return_when=asyncio.FIRST_COMPLETED)
            if ended in done:
                receive.cancel()
                break
            ended.cancel()
            msg = json.loads(receive.result())
            if msg.get("event") == "stop":
                break
            if msg.get("event") == "text" and str(msg.get("text", "")).strip():
                text = str(msg["text"]).strip()
                ev.emit(EventType.STT_RESULT, text=text, source="typed")
                try:
                    await agent.handle(text)
                except Exception as e:
                    ev.emit(EventType.ERROR, level=Level.ERROR, during="agent", error=repr(e))
                    await send({"event": "error", "message": "the agent failed on that message"})
    except WebSocketDisconnect:
        pass
    except Exception as e:
        log.exception("chat test failed: %r", e)
    finally:
        rt.calls.pop(call_id, None)
        ev.emit(EventType.CALL_END, reason="chat_ended")
        try:
            await ws.close()
        except Exception:
            pass
