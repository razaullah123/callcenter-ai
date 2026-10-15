"""Voices page: the voices this project's agents speak with, and a preview in any of them.

    GET  /api/voices             each agent of the project: TTS provider, model and voice per language
    GET  /api/voices/catalog     every voice the project's TTS providers offer, with the agents using each one
    POST /api/voices/preview     {agent?, language, voice?, text?} → a short WAV sample (base64) in that agent's TTS
                                 (voice: try another voice name of the same provider)
    POST /api/voices/use         {agent, language, voice} → the agent's DRAFT speaks with this voice in that language
                                 (goes live when the agent is published — through its test checks)
"""

import base64
import io
import time
import wave

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from runtime.control.accounts import principal
from runtime.control.api import _rt, auth, primary_agent
from runtime.platform import current_project

router = APIRouter(prefix="/api")

SAMPLE = {"ar": "هلا والله، معك مساعد الحبيب. كيف أقدر أخدمك اليوم؟",
          "en": "Hello, this is your voice assistant. How can I help you today?"}


@router.get("/voices", dependencies=auth)
async def voices() -> list[dict]:
    rt = _rt()
    if rt.platform is None:
        agents = [rt.agent]
    else:
        agents = [await rt.loader.for_call(agent_id=a["id"]) for a in await rt.platform.agents(current_project())
                  if a.get("published_release_id")]
    out = []
    rows = {a["id"]: a for a in await rt.platform.agents(current_project())} if rt.platform is not None else {}
    for ag in agents:
        tts = (ag.providers.config or {}).get("tts") or {}
        s = tts.get("settings") or {}
        draft = (rows.get(ag.agent_id) or {}).get("draft")
        draft_tts = (((draft or {}).get("models") or {}).get("tts") or {}).get("settings") or {}
        out.append({"agent": ag.agent_id, "name": ag.name, "provider": tts.get("provider"),
                    "draft_voices": {lang: draft_tts.get(f"voice_{lang}") for lang in ag.languages} if draft else {},
                    "languages": list(ag.languages),
                    "voices": {lang: {"voice": s.get(f"voice_{lang}"), "model": s.get(f"model_{lang}") or s.get("model")}
                               for lang in ag.languages}})
    return out


@router.get("/voices/catalog", dependencies=auth)
async def catalog() -> list[dict]:
    """Hamsa-style voice library: the voices of every TTS provider the project's agents use."""
    from runtime.providers.registry import provider_class
    used = await voices()
    out, seen = [], set()
    for provider in sorted({a["provider"] for a in used if a["provider"]}):
        try:
            cls = provider_class("tts", provider)
        except Exception:
            continue
        for v in getattr(cls, "VOICES", []) or []:
            key = (provider, v["voice"], v["language"])
            if key in seen:
                continue
            seen.add(key)
            users = [{"agent": a["agent"], "name": a["name"]} for a in used if a["provider"] == provider
                     and (a["voices"].get(v["language"]) or {}).get("voice") == v["voice"]]
            drafts = [{"agent": a["agent"], "name": a["name"]} for a in used if a["provider"] == provider
                      and (a.get("draft_voices") or {}).get(v["language"]) == v["voice"]
                      and (a["voices"].get(v["language"]) or {}).get("voice") != v["voice"]]
            out.append({**v, "provider": provider, "used_by": users, "draft_by": drafts})
    return out


class PreviewBody(BaseModel):
    agent: str | None = None
    language: str = "ar"
    voice: str | None = None
    text: str | None = None


@router.post("/voices/preview", dependencies=auth)
async def preview(body: PreviewBody) -> dict:
    ag = await primary_agent(body.agent)
    text = (body.text or SAMPLE.get(body.language, SAMPLE["en"])).strip()[:300]
    t0 = time.perf_counter()
    pcm, rate = b"", 24000
    try:
        async for chunk in ag.tts.synthesize(text, language=body.language, voice=body.voice or None):
            pcm, rate = pcm + chunk.data, chunk.sample_rate
    except Exception as e:
        provider = ((ag.providers.config or {}).get("tts") or {}).get("provider") or "the voice provider"
        if "connection" in type(e).__name__.lower() or "connect" in str(e).lower():
            raise HTTPException(502, f"Can't reach {provider} from the server (network or firewall) — try again later")
        raise HTTPException(502, f"{provider} could not make the sample: {e}"[:300])
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return {"audio": base64.b64encode(buf.getvalue()).decode(), "sample_rate": rate, "text": text,
            "ms": round((time.perf_counter() - t0) * 1000), "seconds": round(len(pcm) / 2 / rate, 2)}


class UseBody(BaseModel):
    agent: str
    language: str
    voice: str


@router.post("/voices/use", dependencies=auth)
async def use_voice(body: UseBody) -> dict:
    """Put a catalogue voice into an agent's draft (Studio → Publish makes it live, through its test checks)."""
    import copy
    from runtime.control.agents_api import _agent, _check, _working
    rt = _rt()
    store = rt.platform
    if store is None:
        raise HTTPException(503, "the platform database is not available")
    a = await _agent(store, body.agent)
    ag = await primary_agent(body.agent)
    tts = (ag.providers.config or {}).get("tts") or {}
    provider = tts.get("provider")
    match = next((v for v in await catalog() if v["provider"] == provider and v["voice"] == body.voice
                  and v["language"] == body.language), None)
    if match is None:
        raise HTTPException(409, f"{body.voice!r} is not a {body.language} voice of {a['name']}'s voice service ({provider})")
    if body.language not in ag.languages:
        raise HTTPException(409, f"{a['name']} doesn't speak {body.language}")
    bundle = await _working(store, a)
    spec = bundle.setdefault("models", {}).setdefault("tts", {})
    settings = spec.setdefault("settings", {})
    before = settings.get(f"voice_{body.language}") or (tts.get("settings") or {}).get(f"voice_{body.language}")
    settings[f"voice_{body.language}"] = body.voice
    if match.get("model"):
        settings[f"model_{body.language}"] = match["model"]
    if errors := await _check(rt, store, body.agent, copy.deepcopy(bundle)):
        raise HTTPException(422, {"errors": errors})
    await store.set_draft(body.agent, bundle, principal().actor)
    await store.audit(body.agent, "voice.changed", principal().actor,
                      {"language": body.language, "from": before, "to": body.voice, "draft": True})
    return {"agent": body.agent, "language": body.language, "voice": body.voice, "previous": before, "draft": True}
