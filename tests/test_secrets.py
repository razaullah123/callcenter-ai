"""Phase 12.2: encrypted secret store, key migration, secret references in providers, console API."""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.config import get_settings
from runtime.platform.loader import AgentLoader
from runtime.platform.secrets import Cipher, Secrets, SecretsUnavailable, generate_master_key, hint, refs_in
from runtime.platform.seed import migrate_secrets, seed
from runtime.platform.store import WORKSPACE, MemoryStore
from runtime.tools import MockMCP

KEY = generate_master_key()


def secrets_for(store, key=KEY, settings=None):
    return Secrets(store, Cipher(key), settings or get_settings())


async def test_cipher_round_trip_and_wrong_key():
    c = Cipher(KEY)
    token = c.encrypt("gsk_live_1234abcd")
    assert "gsk_live" not in token and c.decrypt(token) == "gsk_live_1234abcd"
    with pytest.raises(SecretsUnavailable):
        Cipher(generate_master_key()).decrypt(token)          # MASTER_KEY changed
    with pytest.raises(SecretsUnavailable):
        Cipher(None).encrypt("x")
    assert hint("gsk_live_1234abcd") == "••••abcd" and hint("short") == "••••"


async def test_store_keeps_only_ciphertext():
    store = MemoryStore()
    await store.ensure_workspace(WORKSPACE, "t")
    sec = secrets_for(store)
    await sec.put("OPENAI_KEY", "sk-abcdef123456", "test")
    row = await store.secret(WORKSPACE, "OPENAI_KEY")
    assert "sk-abcdef" not in json.dumps(row, default=str)
    assert await sec.get("OPENAI_KEY") == "sk-abcdef123456"
    assert [r["name"] for r in await store.secrets()] == ["OPENAI_KEY"]
    assert "ciphertext" not in (await store.secrets())[0]
    with pytest.raises(ValueError):
        await sec.put("lower-case", "v", "test")


async def test_externalize_and_resolve():
    store = MemoryStore()
    await store.ensure_workspace(WORKSPACE, "t")
    sec = secrets_for(store)
    stored = await sec.externalize("openai-llm", {"api_key": "sk-one-111111", "base_url": "https://x/v1",
                                                  "headers": {"X-Token": "tok-222222"}}, "console")
    assert stored["api_key"] == {"secret": "OPENAI_LLM_API_KEY"} and stored["base_url"] == "https://x/v1"
    assert stored["headers"]["X-Token"] == {"secret": "OPENAI_LLM_HEADER_X_TOKEN"}
    assert refs_in(stored) == {"OPENAI_LLM_API_KEY", "OPENAI_LLM_HEADER_X_TOKEN"}
    assert (await sec.resolve(stored))["api_key"] == "sk-one-111111"
    # the same value coming back (masked in the console, merged back) keeps the reference, no new write
    before = (await store.secret(WORKSPACE, "OPENAI_LLM_API_KEY"))["updated_at"]
    again = await sec.externalize("openai-llm", {"api_key": "sk-one-111111"}, "console", stored)
    assert again["api_key"] == stored["api_key"]
    assert (await store.secret(WORKSPACE, "OPENAI_LLM_API_KEY"))["updated_at"] == before
    # a new value rotates it
    await sec.externalize("openai-llm", {"api_key": "sk-two-333333"}, "console", stored)
    assert await sec.get("OPENAI_LLM_API_KEY") == "sk-two-333333"
    with pytest.raises(LookupError):
        await sec.resolve({"api_key": {"secret": "NOT_THERE_ANYWHERE"}})


async def test_migration_moves_keys_out_of_env_and_records():
    store = MemoryStore()
    legacy_key = "gsk_typed_in_console_999"

    class Legacy(MemoryStore):
        async def legacy_agent_config(self):
            return {"llm": {"provider": "groq", "settings": {"api_key": legacy_key, "model": "m"}}}
    store = Legacy()
    sec = secrets_for(store)
    report = await seed(store, get_settings(), secrets=sec)
    assert "GROQ_API_KEY" in report["secrets"]["imported"] and "groq-llm" in report["secrets"]["providers"]
    providers = {p["id"]: p for p in await store.providers()}
    assert providers["groq-llm"]["settings"]["api_key"] == {"secret": "GROQ_LLM_API_KEY"}   # its own key
    assert providers["groq-stt"]["settings"]["api_key"] == {"secret": "GROQ_API_KEY"}       # from .env
    assert legacy_key not in json.dumps(await store.providers(), default=str)
    assert providers["custom-http-embedding"]["settings"].get("url") == get_settings().embedding_url
    assert await sec.get("GROQ_LLM_API_KEY") == legacy_key
    # idempotent
    assert (await migrate_secrets(store, get_settings(), sec)) == {}


async def test_migration_converts_12_1_mcp_records():
    store = MemoryStore()
    await store.ensure_workspace(WORKSPACE, "t")
    await store.put_mcp_server({"id": "m", "workspace_id": WORKSPACE, "name": "m", "url": "https://m",
                                "auth": {"header": "Authorization", "scheme": "Bearer", "secret_env": "MCP_AUTH_TOKEN"}})
    report = await migrate_secrets(store, get_settings(), secrets_for(store))
    assert report["mcp_servers"] == ["m"]
    assert (await store.mcp_servers())[0]["auth"]["secret"] == "MCP_AUTH_TOKEN"


async def test_without_master_key_nothing_is_migrated():
    store = MemoryStore()
    report = await seed(store, get_settings(), secrets=secrets_for(store, key=None))
    assert report["secrets"] == {"skipped": "MASTER_KEY not set"}
    assert await store.secrets() == []


async def test_loader_resolves_provider_keys_from_the_store():
    store = MemoryStore()
    sec = secrets_for(store)
    await seed(store, get_settings(), secrets=sec)
    await sec.put("GROQ_API_KEY", "gsk_from_the_store_42", "test")
    mcp = MockMCP()
    await mcp.start()
    agent = await AgentLoader(store, mcp, get_settings(), warm_phrases=False, secrets=sec).for_call()
    assert agent.llm.settings.api_key.get_secret_value() == "gsk_from_the_store_42"


# ---------------------------------------------------------------- console API


class _Rt:
    def __init__(self, store, sec, settings):
        self.platform, self.secrets, self.settings = store, sec, settings
        self.reloads = 0

    async def reload(self):
        self.reloads += 1


@pytest.fixture
def api(monkeypatch):
    import asyncio

    from runtime.control import connections
    from runtime.server import app as server_app
    store = MemoryStore()
    sec = secrets_for(store)
    settings = get_settings().model_copy(update={"console_token": None})
    asyncio.new_event_loop().run_until_complete(seed(store, settings, secrets=sec))
    rt = _Rt(store, sec, settings)
    monkeypatch.setitem(server_app.state, "rt", rt)
    app = FastAPI()
    app.include_router(connections.router)
    with TestClient(app) as c:
        yield c, rt, store


def test_connections_api_never_returns_keys(api):
    c, rt, store = api
    r = c.post("/api/connections", json={"kind": "llm", "type": "openai_compatible", "name": "Local vLLM",
                                         "settings": {"base_url": "http://gpu:8000/v1", "api_key": "sk-local-7777"}})
    assert r.status_code == 200, r.text
    # the model is an agent choice, not part of the connection
    bad = c.post("/api/connections", json={"kind": "llm", "type": "openai_compatible", "name": "Other",
                                           "settings": {"base_url": "http://gpu:8000/v1", "model": "qwen"}})
    assert bad.status_code == 422 and "chosen per agent" in bad.text
    listing = c.get("/api/connections").json()
    assert "sk-local-7777" not in json.dumps(listing)
    row = next(x for x in listing["connections"] if x["id"] == "local-vllm")
    assert row["settings"]["api_key"] == {"secret": "LOCAL_VLLM_API_KEY"} and row["used_by"] == []
    groq = next(x for x in listing["connections"] if x["id"] == "groq-llm")
    assert groq["used_by"] == ["hmg-care"]
    secrets = c.get("/api/secrets").json()
    assert secrets["encryption"] is True
    names = {s["name"]: s for s in secrets["secrets"]}
    assert names["LOCAL_VLLM_API_KEY"]["hint"] == "••••7777"
    assert names["LOCAL_VLLM_API_KEY"]["used_by"] == ["connection local-vllm"]
    assert "sk-local-7777" not in json.dumps(secrets)


def test_connection_rules(api):
    c, rt, store = api
    assert c.delete("/api/connections/groq-llm").status_code == 409            # used by hmg-care
    assert c.post("/api/connections", json={"kind": "llm", "type": "nope", "name": "x"}).status_code == 422
    bad = c.put("/api/connections/groq-llm", json={"settings": {"api_key": {"secret": "MISSING_KEY_X"}}})
    assert bad.status_code == 422 and "MISSING_KEY_X" in bad.text
    ok = c.put("/api/connections/groq-llm", json={"settings": {"api_key": "gsk_rotated_5555", "timeout_s": 12}})
    assert ok.status_code == 200 and ok.json()["used_by"] == ["hmg-care"] and rt.reloads == 1
    assert c.delete("/api/secrets/GROQ_LLM_API_KEY").status_code == 409          # still referenced


def test_secret_rotation(api):
    c, rt, store = api
    assert c.put("/api/secrets/GROQ_API_KEY", json={"value": "gsk_new_value_8888"}).status_code == 200
    assert {s["name"]: s["hint"] for s in c.get("/api/secrets").json()["secrets"]}["GROQ_API_KEY"] == "••••8888"
    assert c.put("/api/secrets/bad name", json={"value": "x"}).status_code == 422
    assert c.put("/api/secrets/UNUSED_ONE", json={"value": "abcdefgh1"}).status_code == 200
    assert c.delete("/api/secrets/UNUSED_ONE").status_code == 200
