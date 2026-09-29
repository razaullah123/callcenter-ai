"""IVR endpoint — drop-in compatible with the existing telephony integration.

    ws://<host>/ws/voice-pipeline?phone_number=<caller or extension>&access_token=<JWT>

  IVR → agent
    binary   raw PCM16 little-endian mono, continuous stream (8 kHz; IVR_INBOUND_RATE)
    text     {"type": "ping"}                          keep-alive, no reply
    text     {"type": "init", "sample_rate": 16000}    optional, overrides the inbound rate
  agent → IVR
    binary   complete WAV files (RIFF + PCM16 mono), ~300 ms pieces paced in real time
             (IVR_CHUNK_MS; resampled to IVR_OUTBOUND_RATE, default 8 kHz — the header states the rate)
    text     {"action": "transfer", "destination": "<extension / queue>"}   hand the call to a human
  The agent closes the socket after its goodbye (end of call).

phone_number: an outside caller's number, or — with 8 digits or fewer — a PBX extension, resolved to its
branch through projects.base_extension (leading 4 digits, else 3). A literal "+" sent unencoded arrives as a
space and is repaired. access_token: HS256 JWT signed with AUTH_SECRET, rejected if expired or its jti is in
blacklisted_tokens (same tokens as the existing IVR integration).
"""

import asyncio
import json
import logging
import re
import uuid

import asyncpg
import jwt
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from runtime.app import Runtime
from runtime.config import Settings
from runtime.data.reference import get_reference, projects_by_extension
from runtime.events import EventType, Level
from runtime.providers.audio import pcm16_to_wav
from runtime.voice.call import VoiceCall
from runtime.voice.player import AudioFormat

from . import CLOSED_ERRORS

log = logging.getLogger("runtime.server.ivr")
router = APIRouter()

REJECTION = {
    "ar": "عذراً، هذه الخدمة غير متاحة لهذا الرقم حالياً. شكراً لاتصالك.",
    "en": "Sorry, this service is not available for this number at the moment. Thank you for calling.",
}

_auth_pool: asyncpg.Pool | None = None


async def _auth_db(settings: Settings) -> asyncpg.Pool | None:
    global _auth_pool
    dsn = settings.auth_database_url or settings.source_database_url
    if _auth_pool is None and dsn is not None:
        try:
            _auth_pool = await asyncpg.create_pool(dsn.get_secret_value(), min_size=1, max_size=4, ssl=False,
                                                   timeout=5)
        except Exception as e:
            log.warning("auth database unavailable: %r", e)
    return _auth_pool


async def verify_access_token(token: str, settings: Settings) -> dict | None:
    """Payload if the JWT is valid, unexpired and not blacklisted; None otherwise."""
    if settings.auth_secret is None:
        log.warning("AUTH_SECRET not set — IVR tokens are NOT verified (development only)")
        return {"sub": "unverified-dev"}
    try:
        payload = jwt.decode(token, settings.auth_secret.get_secret_value(), algorithms=["HS256"])
    except jwt.PyJWTError as e:
        log.warning("IVR token rejected: %s", e)
        return None
    jti = payload.get("jti")
    pool = await _auth_db(settings) if jti else None
    if pool is not None:
        async with pool.acquire() as conn:
            if await conn.fetchval("SELECT 1 FROM blacklisted_tokens WHERE token_jti = $1", jti):
                log.warning("IVR token rejected: blacklisted jti")
                return None
    return payload


async def is_whitelisted(number: str, settings: Settings) -> bool:
    pool = await _auth_db(settings)
    if pool is None:
        return True   # fail open like the existing integration when the DB is unavailable
    tail = re.sub(r"\D", "", number)[-9:]
    async with pool.acquire() as conn:
        return bool(await conn.fetchval(
            "SELECT 1 FROM white_listed_numbers WHERE active AND right(regexp_replace(mobile_number, '\\D', '', 'g'), 9) = $1",
            tail))


def repair_phone(raw: str) -> str:
    """'+9665…' sent without %2B arrives as ' 9665…'."""
    raw = raw or ""
    return ("+" + raw.strip()) if raw.startswith(" ") else raw.strip()


def mask(number: str) -> str:
    digits = re.sub(r"\D", "", number)
    return f"…{digits[-2:]}" if len(digits) > 4 else digits


@router.websocket("/ws/voice-pipeline")
async def voice_pipeline(ws: WebSocket, phone_number: str = "", access_token: str = "") -> None:
    from runtime.server.app import state   # shared runtime + phrase cache
    await ws.accept()
    rt: Runtime = state["rt"]
    s = rt.settings
    if await verify_access_token(access_token, s) is None:
        await ws.close(code=1008, reason="Unauthorized")
        return

    phone = repair_phone(phone_number)
    digits = re.sub(r"\D", "", phone)
    branch = []
    if 0 < len(digits) <= 8:
        try:
            branch = projects_by_extension(await get_reference(), digits)
        except Exception as e:
            log.warning("extension lookup failed: %r", e)

    call_id = f"ivr-{uuid.uuid4().hex[:12]}"
    lock = asyncio.Lock()
    in_fmt = AudioFormat("pcm16", s.ivr_inbound_rate)
    out_fmt = AudioFormat("pcm16", s.ivr_outbound_rate or 24000)
    closing = asyncio.Event()
    call: VoiceCall | None = None

    async def send_audio(pcm: bytes) -> None:
        async with lock:
            try:
                await ws.send_bytes(pcm16_to_wav(pcm, out_fmt.sample_rate))
            except CLOSED_ERRORS:
                pass        # the IVR closed the call while audio was on its way

    async def send_event(msg: dict) -> None:
        kind = msg.get("event")
        if kind == "transfer":
            dest = transfer_destination(call, s)
            async with lock:
                await ws.send_text(json.dumps({"action": "transfer", "destination": dest}))
            rt.bus.bind(call_id=call_id).emit(EventType.HANDOFF, destination=dest, reason=msg.get("reason"))
        elif kind == "hangup":
            closing.set()
        # "clear" / "transcript" / "metric" have no IVR equivalent: pieces are real-time paced, so stopping the
        # stream is the barge-in.

    call = VoiceCall(rt, call_id=call_id, in_fmt=in_fmt, out_fmt=out_fmt, send_audio=send_audio,
                     send_event=send_event, ani=None if branch else (phone or None), phrases=state.get("phrases"),
                     chunk_ms=s.ivr_chunk_ms, aec=s.ivr_aec, barge_in_grace_ms=s.ivr_barge_in_grace_ms,
                     min_suppression_ratio=s.ivr_min_suppression_ratio)
    rt.calls[call_id] = call
    ev = rt.bus.bind(call_id=call_id)
    ev.emit(EventType.SLOT_SET, field="ivr_connect", number=mask(phone), extension_call=bool(branch))
    try:
        if branch:
            call.set_branch(branch)
        elif phone and s.ivr_whitelist and not await is_whitelisted(phone, s):
            ev.emit(EventType.POLICY_BLOCK, reason="not_whitelisted")
            for lang in ("ar", "en"):
                await call.player.say(REJECTION[lang], language=lang)
            await call.player.drained()
            return
        receiver = asyncio.create_task(_receive(ws, call, closing))
        await call.start()
        await closing.wait()
        await call.player.drained()
        receiver.cancel()
    except WebSocketDisconnect:
        pass
    except Exception as e:
        ev.emit(EventType.ERROR, level=Level.ERROR, during="ivr", error=repr(e))
        log.exception("IVR call failed")
    finally:
        rt.calls.pop(call_id, None)
        await call.stop()
        try:
            await ws.close()
        except Exception:
            pass
        log.info("IVR call %s ended (%s) — connection closed", call.session.call_id, call.end_reason)


async def _receive(ws: WebSocket, call: VoiceCall, closing: asyncio.Event) -> None:
    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if msg.get("bytes"):
                await call.on_audio(msg["bytes"])
            elif msg.get("text"):
                try:
                    data = json.loads(msg["text"])
                except ValueError:
                    continue
                if data.get("type") == "init" and data.get("sample_rate"):
                    call.set_input_rate(int(data["sample_rate"]))
                # {"type": "ping"}: keep-alive, no reply
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        closing.set()


def transfer_destination(call: VoiceCall | None, s: Settings) -> str:
    """Configured human-agent destination, else the chosen / dialed branch's base extension."""
    if s.ivr_transfer_destination:
        return s.ivr_transfer_destination
    from runtime.data import reference
    ref = reference._ref
    if call is None or ref is None:
        return ""
    wanted = call.session.slots.get("project_id") or next(iter(call.session.memory.get("branch_projects", [])), None)
    for p in ref.projects:
        if p.reference_id == wanted and p.base_extension:
            return p.base_extension
    return ""
