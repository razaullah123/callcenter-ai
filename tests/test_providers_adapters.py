import json

import httpx
import pytest

from runtime.providers import AudioInput, available, create, schemas
from runtime.providers import custom_http
from runtime.providers.audio import pcm16_to_wav


def test_all_adapters_registered():
    a = available()
    assert {"groq", "openai_compatible", "custom_http"} <= set(a["stt"])
    assert {"groq", "openai_compatible", "custom_http"} <= set(a["tts"])
    assert {"groq", "openai_compatible"} <= set(a["llm"])
    assert "openai_compatible" in a["embedding"]
    # Required fields show up in the schema the console renders.
    assert "base_url" in schemas()["llm"]["openai_compatible"]["required"]


def _mock(monkeypatch, handler):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(custom_http, "_http", lambda timeout: client)


async def test_custom_http_stt_contract(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = request.content
        return httpx.Response(200, json={"text": " مرحبا ", "language": "ar"})

    _mock(monkeypatch, handler)
    stt = create("stt", "custom_http", {"url": "http://stt.local/transcribe",
                                        "headers": {"Authorization": "Bearer x"}})
    t = await stt.transcribe(AudioInput(b"\x00\x00" * 800, 8000), prompt="العليا")
    assert t.text == "مرحبا" and t.language == "ar"
    assert seen["auth"] == "Bearer x" and b"RIFF" in seen["body"] and "العليا".encode() in seen["body"]


async def test_custom_http_tts_wav_and_mulaw(monkeypatch):
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        if body["encoding"] == "mulaw":
            return httpx.Response(200, content=b"\x7f" * 160, headers={"content-type": "audio/basic"})
        return httpx.Response(200, content=pcm16_to_wav(b"\x01\x00" * 50, 16000),
                              headers={"content-type": "audio/wav"})

    _mock(monkeypatch, handler)
    tts = create("tts", "custom_http", {"url": "http://tts.local", "voice_ar": "najdi_f"})

    [wav] = [c async for c in tts.synthesize("هلا", language="ar")]
    assert wav.encoding == "pcm16" and wav.sample_rate == 16000 and len(wav.data) == 100
    assert requests[0]["voice"] == "najdi_f"

    [mu] = [c async for c in tts.synthesize("hello", language="en", encoding="mulaw", sample_rate=8000)]
    assert mu.encoding == "mulaw" and mu.sample_rate == 8000 and len(mu.data) == 160



@pytest.mark.parametrize("shape", [
    lambda v: v,
    lambda v: [v],
    lambda v: {"embedding": v},
    lambda v: {"embeddings": [v]},
    lambda v: {"data": [{"index": 0, "embedding": v}]},
])
async def test_custom_http_embedding_accepts_common_shapes(monkeypatch, shape):
    vec = [0.1] * 4

    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content) == {"text": "حي النرجس"}
        return httpx.Response(200, json=shape(vec))

    _mock(monkeypatch, handler)
    emb = create("embedding", "custom_http", {"url": "http://emb.local", "dim": 4})
    assert await emb.embed(["حي النرجس"]) == [vec]


async def test_custom_http_embedding_rejects_wrong_dim(monkeypatch):
    _mock(monkeypatch, lambda r: httpx.Response(200, json={"embedding": [0.0] * 3}))
    emb = create("embedding", "custom_http", {"url": "http://emb.local", "dim": 1024})
    with pytest.raises(ValueError, match="wrong model"):
        await emb.embed(["x"])
