"""Live hub: fans out events to console WebSocket clients and tracks active calls in memory."""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from runtime.events import Event, EventType

log = logging.getLogger(__name__)
ACTIVE_TTL_S = 30 * 60


@dataclass
class ActiveCall:
    call_id: str
    started: float = field(default_factory=time.time)
    last_event: float = field(default_factory=time.time)
    skill: str | None = None
    step: str | None = None
    turns: int = 0
    language: str | None = None
    verified: bool = False
    slots: dict[str, Any] = field(default_factory=dict)
    last_user: str | None = None
    last_agent: str | None = None
    agent_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {**self.__dict__, "duration_s": round(time.time() - self.started)}


class LiveHub:
    def __init__(self) -> None:
        self.active: dict[str, ActiveCall] = {}
        self._ended: dict[str, float] = {}      # call_id → when it ended: late events must not revive it
        self._clients: set[tuple[asyncio.Queue, str | None, Any]] = set()
        self._call_agent: dict[str, str | None] = {}    # call → agent (from call.start): a console sees its project's

    def visible(self, call_id: str | None, scope) -> bool:
        """`scope` = (agent ids, include calls without an agent) or None for everything."""
        if scope is None or not call_id:
            return scope is None
        agents, unassigned = scope
        agent = self._call_agent.get(call_id)
        return agent in agents if agent else unassigned

    async def __call__(self, e: Event) -> None:
        before = set(self.active)
        self._track(e)
        if not self._clients:
            return
        msg = json.dumps({"kind": "event", "event": e.model_dump(mode="json")}, ensure_ascii=False, default=str)
        for q, call_filter, scope in list(self._clients):
            if call_filter and e.call_id != call_filter:
                continue
            if not call_filter and not self.visible(e.call_id, scope):
                continue
            if q.qsize() < 1000:        # slow client: drop rather than grow unbounded
                q.put_nowait(msg)
        if set(self.active) != before:  # a call started / ended: every console updates "Active now" at once
            self.broadcast_active()

    def active_message(self, scope=None) -> str:
        return json.dumps({"kind": "active", "calls": self.active_calls(scope)}, ensure_ascii=False, default=str)

    def broadcast_active(self) -> None:
        for q, _, scope in list(self._clients):
            if q.qsize() < 1000:
                q.put_nowait(self.active_message(scope))

    def _track(self, e: Event) -> None:
        cid = e.call_id
        if not cid:
            return
        now = time.time()
        if e.type in (EventType.CALL_START, EventType.AGENT_TRANSFER):
            self._call_agent[cid] = e.data.get("agent_id")
        if e.type == EventType.CALL_END:
            self.active.pop(cid, None)
            self._ended[cid] = now
            for old in [k for k, t in self._ended.items() if now - t > ACTIVE_TTL_S]:
                self._ended.pop(old, None)
                self._call_agent.pop(old, None)
            return
        if cid in self._ended:
            # e.g. the cancelled reply's last "turn.end" after the caller hung up mid-sentence — before, this
            # re-created the call and the console showed a ghost "1 live call" for 30 minutes
            return
        c = self.active.setdefault(cid, ActiveCall(cid))
        c.last_event = time.time()
        d = e.data
        if e.skill:
            c.skill = e.skill
        if e.type == EventType.TURN_START:
            c.turns = e.turn_id or c.turns
            c.last_user = d.get("text")
        elif e.type == EventType.AGENT_SAY:
            c.last_agent = d.get("text")
        elif e.type == EventType.STEP_TRANSITION:
            c.step = d.get("step")
        elif e.type == EventType.SKILL_ENTER:
            c.skill = d.get("skill")
        elif e.type == EventType.SKILL_EXIT and d.get("reason") == "verified":
            c.verified = True
        elif e.type == EventType.SLOT_SET:
            if d.get("field") == "language":
                c.language = d.get("value")
            elif d.get("field"):
                c.slots[d["field"]] = d.get("value")
        elif e.type == EventType.CALL_START:
            c.language = d.get("language")
            c.agent_id = d.get("agent_id")
        elif e.type == EventType.AGENT_TRANSFER:
            c.agent_id = d.get("agent_id") or c.agent_id
        # forget calls that went silent without a call.end (e.g. crashed client)
        now = time.time()
        for stale in [k for k, v in self.active.items() if now - v.last_event > ACTIVE_TTL_S]:
            self.active.pop(stale, None)

    def subscribe(self, call_id: str | None = None, scope=None) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        if scope is not None:                       # the client set needs a hashable scope
            scope = (tuple(scope[0]), scope[1])
        self._clients.add((q, call_id, scope))
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._clients = {(cq, f, s) for cq, f, s in self._clients if cq is not q}

    def active_calls(self, scope=None) -> list[dict[str, Any]]:
        return sorted((c.as_dict() for c in self.active.values() if self.visible(c.call_id, scope)),
                      key=lambda c: -c["started"])
