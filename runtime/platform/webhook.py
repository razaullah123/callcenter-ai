"""Call webhook, in Hamsa's format (docs.tryhamsa.com → Agents → Webhooks, Developers → Webhook Integration).

An agent's release may carry `webhook: {url, auth, events, include_transcript}`. The platform POSTs one JSON envelope per
event, in order per call:

    {"eventType": "call.ended", "callId": "...", "timestamp": "<ISO>", "projectId": "...", "agentId": "...",
     "agentName": "...", "data": {"timestamp": "<ISO>", "data": {…event data…}}}

Events (`EVENTS`): call.started / call.answered (the call connected — both fire then), transcription.update
({"speaker": "User"|"Agent", "text"}), tool.executed ({"toolName", "input", "output": null, "success", "duration"} — results
are not kept in the event stream), call.ended ({"conversationId", "conversationRecording": null, "transcription":
[{"Agent": text}, {"User": text}], "outcomeResult": {}, plus our "call" summary}). Recordings and outcome data are not
produced yet. Everything comes from the (already PII-masked) event stream — the caller's number is never sent.

`auth` is `{"secret": NAME}` (sent as `Authorization: Bearer <value>`); HTTPS URLs only, like Hamsa. Delivery is in the
background: 3 attempts (waits 2 s, 4 s) with a 5 s timeout, retried on network errors, 429 and 5xx; a failure becomes a
WARNING event and never touches the call. Every delivery (event, call, outcome, attempts) is kept in the agent's delivery
history (`webhook_deliveries`, the latest 200).

When the agent has post-call analysis on, `call.ended` waits (up to 35 s) for it and adds `analysis` (summary,
sentiment, csat, nps, resolved) and the agent's own outcome fields to `outcomeResult`.

Hamsa's custom parameters are echoed back: the `params` the call started with come back in `call.ended`'s `outcomeResult`
(and in `call.started` / `call.answered`), so a receiver can match the call to its own records without keeping state.

Extras (not in Hamsa): headers `X-Webhook-Event` and `X-Webhook-Id` (one id per event, the same on every retry — use it to
de-duplicate), and optional signing: with `signing: {"secret": NAME}` each request carries `X-Webhook-Timestamp` and
`X-Webhook-Signature: sha256=<hex>`, the HMAC-SHA256 of `"<timestamp>.<raw body>"` under that secret.
"""

import asyncio
import hashlib
import hmac
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx

from runtime.events import Event, EventType, Level

log = logging.getLogger(__name__)

EVENTS = ("call.started", "call.answered", "transcription.update", "tool.executed", "call.ended")
RETRY_DELAYS_S = (2.0, 4.0)              # between the three attempts
TIMEOUT_S = 5.0
HISTORY_KEEP = 200
CALL_FIELDS = ("channel", "language", "started_at", "ended_at", "duration_s", "status", "end_reason", "verified",
               "booked", "turns", "handoff")


def webhook_errors(cfg: Any) -> list[str]:
    """Problems with a bundle's `webhook` section (empty/absent = no webhook)."""
    if not cfg:
        return []
    if not isinstance(cfg, dict):
        return ["webhook must be an object"]
    errors = []
    url = cfg.get("url")
    if url and not (isinstance(url, str) and url.startswith("https://")):
        errors.append("webhook.url must be an https:// address (HTTP and local addresses are not accepted)")
    auth = cfg.get("auth")
    if auth and not (isinstance(auth, dict) and isinstance(auth.get("secret"), str) and auth["secret"]):
        errors.append('webhook.auth must be {"secret": NAME}')
    signing = cfg.get("signing")
    if signing and not (isinstance(signing, dict) and isinstance(signing.get("secret"), str) and signing["secret"]):
        errors.append('webhook.signing must be {"secret": NAME}')
    events = cfg.get("events")
    if events is not None and not (isinstance(events, list) and all(e in EVENTS for e in events)):
        errors.append(f"webhook.events must be a list of: {', '.join(EVENTS)}")
    return errors


def build_payload(event: str, call_id: str, data: dict, *, agent_id: str | None, agent_name: str | None = None,
                  project_id: str | None = None, now: datetime | None = None) -> dict:
    ts = (now or datetime.now(timezone.utc)).isoformat()
    return {"eventType": event, "callId": call_id, "timestamp": ts, "projectId": project_id, "agentId": agent_id,
            "agentName": agent_name, "data": {"timestamp": ts, "data": data}}


def sign(secret: str, timestamp: str, body: bytes) -> str:
    """`X-Webhook-Signature` value: HMAC-SHA256 of "<timestamp>.<body>"."""
    return "sha256=" + hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()


def ended_data(call_id: str, call: dict, transcript: list[dict], include_transcript: bool = True,
               params: dict | None = None, analysis: dict | None = None) -> dict:
    lines = [{"Agent" if t["role"] == "agent" else "User": t["text"]} for t in transcript if t["role"] in ("agent", "user")]
    data = {"conversationId": call_id, "conversationRecording": None,
            "transcription": lines if include_transcript else [],
            "outcomeResult": {**(params or {}), **((analysis or {}).get("outcome") or {})},
            "call": {k: call[k] for k in CALL_FIELDS if k in call}}
    if analysis and analysis.get("status") == "ok":            # the AI's reading of the call (estimates for csat / nps)
        data["analysis"] = {k: analysis.get(k) for k in ("summary", "sentiment", "csat", "nps", "resolved")}
    return data


class WebhookSink:
    """EventBus subscriber: delivers a call's events to its agent's webhook. The webhook of the release the call starts on
    is kept for the whole call; events of one call are sent one after the other, in order."""

    def __init__(self, rt, *, delays: tuple[float, ...] = RETRY_DELAYS_S) -> None:
        self.rt = rt
        self.delays = delays
        self._tasks: set[asyncio.Task] = set()
        self._client: httpx.AsyncClient | None = None
        self._calls: dict[str, dict] = {}              # call_id → {"cfg", "agent_id", "release_id", "lock", "tool_args"}

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=TIMEOUT_S, follow_redirects=False)
        return self._client

    def _spawn(self, coro, name: str) -> None:
        t = asyncio.create_task(coro, name=name)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    async def __call__(self, e: Event) -> None:
        if not e.call_id or self.rt.platform is None:
            return
        if e.type == EventType.CALL_START:
            live = (getattr(self.rt, "calls", None) or {}).get(e.call_id)       # the call's own params (never in the event log)
            params = dict(getattr(getattr(live, "session", None), "params", None) or {})
            c = {"cfg": None, "lock": asyncio.Lock(), "tool_args": {}, "params": params,
                 "agent_id": e.data.get("agent_id"), "release_id": e.data.get("release_id")}
            self._calls[e.call_id] = c
            data = {"language": e.data.get("language"), "params": params}
            for event in ("call.started", "call.answered"):        # created in order; the lock keeps that order
                self._spawn(self._send(e.call_id, c, event, data), f"webhook-{event}-{e.call_id}")
            return
        c = self._calls.get(e.call_id)
        if c is None:                                   # not a call of this worker, or already over
            return
        if e.type == EventType.CALL_END:
            del self._calls[e.call_id]
            self._spawn(self._ended(e, c), f"webhook-end-{e.call_id}")
        elif e.type == EventType.TOOL_START:
            c["tool_args"][e.data.get("tool")] = e.data.get("args")
        elif e.type in (EventType.TOOL_END, EventType.TOOL_ERROR):
            self._queue(e, c, "tool.executed", {
                "toolName": e.data.get("tool"), "input": c["tool_args"].pop(e.data.get("tool"), None), "output": None,
                "success": e.type == EventType.TOOL_END, "duration": e.latency_ms, "error": e.data.get("error")})
        elif e.type == EventType.TURN_START and e.data.get("text"):
            self._queue(e, c, "transcription.update", {"speaker": "User", "text": e.data["text"]})
        elif e.type == EventType.AGENT_SAY and e.data.get("text"):
            self._queue(e, c, "transcription.update", {"speaker": "Agent", "text": e.data["text"]})

    def _queue(self, e: Event, c: dict, event: str, data: dict) -> None:
        self._spawn(self._send(e.call_id, c, event, data), f"webhook-{event}-{e.call_id}")

    async def _config(self, c: dict) -> dict | None:
        if c["cfg"] is None:
            release = await self.rt.platform.release(c["release_id"]) if c["release_id"] else None
            cfg = ((release or {}).get("bundle") or {}).get("webhook") or {}
            c["cfg"] = cfg if cfg.get("url") else {}
        return c["cfg"] or None

    async def _send(self, call_id: str, c: dict, event: str, data: dict | None, *, ended: dict | None = None) -> None:
        try:
            async with c["lock"]:                          # FIFO: events of a call go out in the order they happened
                cfg = await self._config(c)
                if not cfg or event not in (cfg.get("events") or EVENTS):
                    return
                agent_id = c["agent_id"]
                if ended is not None:                      # call.ended is built once the call row holds the end
                    store = getattr(self.rt, "store", None)
                    if store is not None:
                        await store.flush()
                    from runtime.control.store import get_call
                    found = await get_call(call_id)
                    if found is None:
                        return
                    agent_id = found["call"].get("agent_id") or agent_id
                    sink = getattr(self.rt, "analysis", None)
                    row = await sink.result(call_id, 35.0) if sink is not None else None     # call.ended carries the analysis
                    data = ended_data(call_id, found["call"], found["transcript"], cfg.get("include_transcript") is not False,
                                      c.get("params"), row)
                agent = await self.rt.platform.agent(agent_id) if agent_id else None
                payload = build_payload(event, call_id, data or {}, agent_id=agent_id, agent_name=(agent or {}).get("name"),
                                        project_id=(agent or {}).get("workspace_id"))
                ok, detail, attempts = await self._deliver(cfg, payload)
                await self.record(agent_id, (agent or {}).get("workspace_id"), call_id, event, ok, detail, attempts)
                if not ok:
                    self.rt.bus.emit(Event(type=EventType.ERROR, level=Level.WARNING, call_id=call_id,
                                           data={"during": "call_webhook", "event": event, "error": detail}))
        except asyncio.CancelledError:
            raise
        except Exception as err:                           # noqa: BLE001 — never affects the call
            log.warning("call webhook %s for %s failed: %r", event, call_id, err)

    async def _ended(self, e: Event, c: dict) -> None:
        await self._send(e.call_id, c, "call.ended", None, ended=e.data)

    async def record(self, agent_id, workspace_id, call_id: str, event: str, ok: bool, detail: str, attempts: int) -> None:
        """One line of the agent's delivery history. Never raises."""
        try:
            if agent_id and workspace_id:
                await self.rt.platform.add_webhook_delivery({
                    "agent_id": agent_id, "workspace_id": workspace_id, "call_id": call_id, "event": event, "ok": ok,
                    "detail": detail[:300], "attempts": attempts})
        except Exception as err:                           # noqa: BLE001
            log.warning("webhook delivery not recorded: %r", err)

    async def deliver(self, cfg: dict, payload: dict, **kw) -> tuple[bool, str]:
        """POST the payload (retrying). Returns (delivered, "HTTP 200" or the reason it failed)."""
        ok, detail, _ = await self._deliver(cfg, payload, **kw)
        return ok, detail

    async def _deliver(self, cfg: dict, payload: dict, *, token: str | None = None,
                       signing_secret: str | None = None) -> tuple[bool, str, int]:
        """(delivered, detail, attempts). `token` / `signing_secret`: plain values for a test, instead of the stored secrets."""
        body = json.dumps(payload, ensure_ascii=False, default=str).encode()
        ts = str(int(time.time()))
        headers = {"Content-Type": "application/json", "X-Webhook-Event": str(payload.get("eventType", "")),
                   "X-Webhook-Id": str(uuid.uuid4()), "X-Webhook-Timestamp": ts}
        secrets = getattr(self.rt, "secrets", None)
        try:
            if token is None and cfg.get("auth"):
                token = await secrets.resolve(cfg["auth"]) if secrets else None
            if signing_secret is None and cfg.get("signing"):
                signing_secret = await secrets.resolve(cfg["signing"]) if secrets else None
        except LookupError as err:
            return False, str(err), 0
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if signing_secret:
            headers["X-Webhook-Signature"] = sign(signing_secret, ts, body)
        detail = ""
        attempts = 0
        for attempt in range(len(self.delays) + 1):
            if attempt:
                await asyncio.sleep(self.delays[attempt - 1])
            attempts += 1
            try:
                r = await self._http().post(cfg["url"], content=body, headers=headers)
            except Exception as err:                       # noqa: BLE001
                detail = f"the URL could not be reached ({err.__class__.__name__})"
                continue
            detail = f"HTTP {r.status_code}"
            if r.is_success:
                return True, detail, attempts
            if r.status_code != 429 and r.status_code < 500:
                break                                      # the receiver refused it: retrying won't help
        return False, detail, attempts

    async def close(self) -> None:
        for t in list(self._tasks):
            t.cancel()
        if self._client is not None:
            await self._client.aclose()
