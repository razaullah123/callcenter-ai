from types import SimpleNamespace as NS

import pytest

from runtime.providers import (
    AudioInput,
    LLMDone,
    TextDelta,
    ToolCallsReady,
    available,
    create,
    schemas,
)
from runtime.providers import groq_provider
from runtime.providers.audio import pcm16_to_wav, split_for_tts, wav_to_pcm16

# ---------- fakes ----------


def _chunk(content=None, tool_calls=None, finish=None, usage=None):
    delta = NS(content=content, tool_calls=tool_calls)
    return NS(choices=[NS(delta=delta, finish_reason=finish)],
              x_groq=NS(usage=NS(model_dump=lambda: usage)) if usage else None)


def _tc(index, id=None, name=None, args=None):
    return NS(index=index, id=id, function=NS(name=name, arguments=args))


class _AsyncIter:
    def __init__(self, items):
        self.items = list(items)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.items:
            raise StopAsyncIteration
        return self.items.pop(0)


class FakeGroq:
    def __init__(self, chunks=None, wav=b"", transcript=None):
        self.calls: list[dict] = []
        outer = self

        async def chat_create(**kw):
            outer.calls.append(kw)
            return _AsyncIter(chunks or [])

        async def speech_create(**kw):
            outer.calls.append(kw)

            async def read():
                return wav if kw["response_format"] == "wav" else b"\xff" * 80
            return NS(read=read)

        async def stt_create(**kw):
            outer.calls.append(kw)
            return NS(text=transcript["text"], model_dump=lambda: transcript)

        self.chat = NS(completions=NS(create=chat_create))
        self.audio = NS(speech=NS(create=speech_create), transcriptions=NS(create=stt_create))


@pytest.fixture
def fake(monkeypatch):
    holder = {}

    def install(**kw):
        client = FakeGroq(**kw)
        monkeypatch.setattr(groq_provider, "_client", lambda *a, **k: client)
        holder["c"] = client
        return client
    return install


async def collect(agen):
    return [e async for e in agen]

# ---------- registry ----------


def test_registry_lists_groq_and_exposes_schemas():
    for kind in ("stt", "llm", "tts"):
        assert "groq" in available()[kind]
    s = schemas()
    assert "reasoning_effort" in s["llm"]["groq"]["properties"]
    with pytest.raises(ValueError, match="unknown llm provider"):
        create("llm", "nope")


def test_defaults_come_from_env_and_config_overrides():
    from runtime.config import get_settings
    llm = create("llm", "groq")
    assert llm.settings.model == get_settings().groq_llm_model
    assert create("llm", "groq", {"model": "llama-3.3-70b-versatile"}).settings.model == "llama-3.3-70b-versatile"

# ---------- LLM ----------


async def test_llm_streams_text_and_assembles_tool_calls(fake):
    client = fake(chunks=[
        _chunk(content="لحظة "),
        _chunk(content="أشيك لك"),
        _chunk(tool_calls=[_tc(0, id="call_1", name="mssql_get_clinics_", args='{"projec')]),
        _chunk(tool_calls=[_tc(0, name="for_project", args='tId": 15}')]),
        _chunk(finish="tool_calls", usage={"total_tokens": 42}),
    ])
    llm = create("llm", "groq", {"model": "openai/gpt-oss-120b"})
    events = await collect(llm.stream([{"role": "user", "content": "hi"}],
                                      tools=[{"type": "function", "function": {"name": "x"}}]))

    assert [e.text for e in events if isinstance(e, TextDelta)] == ["لحظة ", "أشيك لك"]
    [ready] = [e for e in events if isinstance(e, ToolCallsReady)]
    assert ready.calls[0].name == "mssql_get_clinics_for_project"
    assert ready.calls[0].arguments == {"projectId": 15}
    done = events[-1]
    assert isinstance(done, LLMDone) and done.finish_reason == "tool_calls" and done.usage == {"total_tokens": 42}

    sent = client.calls[0]
    assert sent["stream"] is True and sent["reasoning_effort"] == "low" and sent["include_reasoning"] is False
    assert sent["tool_choice"] == "auto"


async def test_llm_invalid_tool_json_is_surfaced_not_raised(fake):
    fake(chunks=[_chunk(tool_calls=[_tc(0, id="c", name="t", args="{bad")]), _chunk(finish="tool_calls")])
    events = await collect(create("llm", "groq").stream([]))
    [ready] = [e for e in events if isinstance(e, ToolCallsReady)]
    assert ready.calls[0].arguments == {"__invalid_json__": "{bad"}


async def test_llm_non_reasoning_model_omits_reasoning_params(fake):
    client = fake(chunks=[_chunk(content="ok", finish="stop")])
    await collect(create("llm", "groq", {"model": "llama-3.3-70b-versatile"}).stream([]))
    assert "reasoning_effort" not in client.calls[0] and "tools" not in client.calls[0]

# ---------- STT ----------


async def test_stt_maps_language_and_sends_wav(fake):
    client = fake(transcript={"text": " أبي أحجز موعد ", "language": "Arabic"})
    stt = create("stt", "groq", {"default_prompt": "مستشفى العليا"})
    t = await stt.transcribe(AudioInput(b"\x00\x00" * 16000, 16000))
    assert t.text == "أبي أحجز موعد" and t.language == "ar" and round(t.duration_ms) == 1000
    sent = client.calls[0]
    assert sent["file"][1][:4] == b"RIFF" and sent["prompt"] == "مستشفى العليا" and "language" not in sent

# ---------- TTS ----------


async def test_tts_splits_long_text_and_decodes_wav(fake):
    wav = pcm16_to_wav(b"\x01\x00" * 100, 24000)
    client = fake(wav=wav)
    tts = create("tts", "groq", {"max_chars": 40, "stream": False})
    text = "أهلاً وسهلاً. حجزت لك الموعد عند الدكتور أحمد، يوم الأحد الساعة عشر الصبح."
    chunks = await collect(tts.synthesize(text, language="ar"))
    assert len(chunks) == len(client.calls) > 1
    assert all(c.encoding == "pcm16" and c.sample_rate == 24000 and len(c.data) == 200 for c in chunks)
    assert all(len(call["input"]) <= 40 for call in client.calls)
    assert client.calls[0]["model"] == tts.settings.model_ar and client.calls[0]["voice"] == tts.settings.voice_ar


async def test_tts_english_voice_and_mulaw_for_telephony(fake):
    client = fake(wav=pcm16_to_wav(b"\x10\x00" * 2400, 24000))  # 100 ms @ 24 kHz
    tts = create("tts", "groq", {"stream": False})
    [chunk] = await collect(tts.synthesize("Your appointment is booked.", language="en",
                                           encoding="mulaw", sample_rate=8000))
    assert chunk.encoding == "mulaw" and chunk.sample_rate == 8000 and len(chunk.data) == 800  # 100 ms @ 8 kHz
    call = client.calls[0]
    assert call["response_format"] == "wav"  # Orpheus is WAV-only; transcoded locally
    assert call["model"] == tts.settings.model_en and call["voice"] == tts.settings.voice_en

# ---------- audio helpers ----------


def test_wav_roundtrip():
    pcm = bytes(range(200))
    assert wav_to_pcm16(pcm16_to_wav(pcm, 16000)) == (pcm, 16000)


@pytest.mark.parametrize("text,limit", [
    ("قصير", 50),
    ("جملة أولى. جملة ثانية؟ جملة ثالثة!", 15),
    ("كلمة " * 60, 30),
])
def test_split_for_tts_respects_limit_and_keeps_words(text, limit):
    chunks = split_for_tts(text, limit)
    assert all(0 < len(c) <= limit for c in chunks)
    assert " ".join(chunks).split() == text.split()


def test_mulaw_codec_is_bit_exact_with_reference():
    import warnings

    import numpy as np
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        import audioop
    from runtime.providers.audio import mulaw_to_pcm16, pcm16_to_mulaw
    pcm = np.arange(-32768, 32768, dtype=np.int16).tobytes()
    assert pcm16_to_mulaw(pcm) == audioop.lin2ulaw(pcm, 2)
    assert mulaw_to_pcm16(bytes(range(256))) == audioop.ulaw2lin(bytes(range(256)), 2)


def test_resample_lengths():
    from runtime.providers.audio import resample_pcm16
    one_sec_24k = b"\x00\x00" * 24000
    assert len(resample_pcm16(one_sec_24k, 24000, 8000)) == 16000
    assert len(resample_pcm16(one_sec_24k, 24000, 16000)) == 32000


def test_streamed_wav_parsing_and_conversion_match_whole_file():
    import numpy as np

    from runtime.providers.audio import StreamConverter, WavStreamParser, resample_pcm16
    pcm = (np.sin(np.arange(24000) / 10) * 8000).astype(np.int16).tobytes()      # 1 s @ 24 kHz
    wav = pcm16_to_wav(pcm, 24000)
    parser, out, conv = WavStreamParser(), b"", None
    for i in range(0, len(wav), 999):                       # odd chunk sizes, header split across chunks
        data = parser.feed(wav[i:i + 999])
        if data:
            conv = conv or StreamConverter(parser.sample_rate, "pcm16", 16000)
            out += conv.feed(data)
    out += conv.flush()
    assert parser.sample_rate == 24000
    assert abs(len(out) - len(resample_pcm16(pcm, 24000, 16000))) <= 64       # same length within a few samples
    mu = StreamConverter(24000, "mulaw", 8000)
    assert abs(len(mu.feed(pcm) + mu.flush()) - 8000) <= 32                     # 1 byte / sample @ 8 kHz


def test_effective_settings_hide_secrets_but_show_values():
    from runtime.control import config_store
    from runtime.control.config_store import ProviderSet
    from runtime.providers import create
    llm = create("llm", "groq", {"api_key": "gsk_secret_value", "model": "openai/gpt-oss-120b"})
    ps = ProviderSet(1, {"embedding": {"provider": "custom_http", "settings": {"url": "http://emb/api",
                                                                            "headers": {"Authorization": "Bearer x"}}}},
                     llm, create("stt", "fake"), create("tts", "fake"))
    eff = config_store.effective(ps)
    assert eff["llm"]["api_key"] == "set" and eff["llm"]["model"] == "openai/gpt-oss-120b"
    assert eff["llm"]["max_tokens"] == 400                            # not mistaken for a secret
    assert eff["embedding"]["url"] == "http://emb/api" and eff["embedding"]["headers"] == {"Authorization": "••••"}
    assert "gsk_secret_value" not in str(eff) and "Bearer x" not in str(eff)


# ---------------------------------------------------------------- hedged LLM requests

class _SlowThenFast:
    """Attempt #n waits delays[n] seconds before answering `answers[n]` (an exception = that attempt fails)."""
    def __init__(self, delays, answers):
        self.delays, self.answers, self.calls = delays, answers, 0

    async def stream(self, messages, *, tools=None, **_):
        import asyncio
        from runtime.providers import LLMDone, TextDelta
        n = self.calls
        self.calls += 1
        await asyncio.sleep(self.delays[n])
        if isinstance(self.answers[n], Exception):
            raise self.answers[n]
        yield TextDelta(self.answers[n])
        yield LLMDone("stop")


async def _text(llm):
    from runtime.providers import TextDelta
    return "".join([e.text async for e in llm.stream([{"role": "user", "content": "hi"}]) if isinstance(e, TextDelta)])


async def test_hedge_races_a_backup_when_the_first_is_slow():
    from runtime.providers.hedge import hedged
    seen = []
    inner = _SlowThenFast([1.0, 0.01], ["first", "backup"])
    assert await _text(hedged(inner, 0.05, seen.append)) == "backup" and inner.calls == 2
    assert seen[0]["after_ms"] >= 40 and seen[-1]["winner"] == "backup"


async def test_hedge_not_used_when_the_first_is_fast():
    from runtime.providers.hedge import hedged
    inner = _SlowThenFast([0.0, 0.0], ["first", "backup"])
    assert await _text(hedged(inner, 0.5)) == "first" and inner.calls == 1


async def test_hedge_survives_one_failed_attempt_and_raises_when_both_fail():
    import pytest
    from runtime.providers.hedge import hedged
    inner = _SlowThenFast([0.2, 0.01], ["first", RuntimeError("503")])       # backup fails, first still answers
    assert await _text(hedged(inner, 0.05)) == "first"
    inner = _SlowThenFast([0.2, 0.01], [RuntimeError("a"), RuntimeError("b")])
    with pytest.raises(RuntimeError):
        await _text(hedged(inner, 0.05))
