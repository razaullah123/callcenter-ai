"""Platform (Phase 12.1): seeding, repo sync, agent loading, routing, per-agent phrases / knobs / providers."""

import copy

import pytest

from runtime.config import get_settings
from runtime.events import EventBus
from runtime.harness.engine import Agent
from runtime.harness.prompts import GREETING, Phrases
from runtime.harness.session import Session
from runtime.platform import bundle as bundle_mod
from runtime.platform import seed as seed_mod
from runtime.platform.bundle import DEFAULT_AGENT, agent_settings, repo_bundle, split_settings
from runtime.platform.loader import AgentLoader, mcp_config
from runtime.platform.seed import seed
from runtime.platform.store import WORKSPACE, MemoryStore
from runtime.tools import MockMCP

from .test_harness import Output, ScriptedLLM


class LegacyStore(MemoryStore):
    """A database that still has the Phase-9 provider config (agent_config)."""

    def __init__(self, legacy):
        super().__init__()
        self._legacy = legacy

    async def legacy_agent_config(self):
        return self._legacy


@pytest.fixture
async def backend():
    mcp = MockMCP()
    await mcp.start()
    return mcp


def loader_for(store, backend, **kw):
    return AgentLoader(store, backend, get_settings(), warm_phrases=False, **kw)


async def published(store, agent_id=DEFAULT_AGENT["id"]):
    agent = await store.agent(agent_id)
    return await store.release(agent["published_release_id"])


# ---------------------------------------------------------------- seeding


async def test_seed_moves_the_hmg_agent_into_the_platform():
    store = MemoryStore()
    report = await seed(store, get_settings())
    assert report["workspace_created"] and report["agent_created"]["version"] == 1
    assert report["agent_created"]["agent"] == "hmg-care"
    kinds = {p["kind"]: p for p in await store.providers()}
    assert set(kinds) == {"stt", "llm", "tts", "embedding"} and kinds["llm"]["type"] == "groq"
    assert (await store.mcp_servers())[0]["auth"]["secret"] == "MCP_AUTH_TOKEN"   # a reference, not the token
    assert {t["name"] for t in await store.tools()} >= {"api_book_Appointment", "find_hospital_by_name"}
    rel = await published(store)
    b = rel["bundle"]
    assert set(b["skills"]) == set(bundle_mod.repo_skill_files())
    assert all(v == 1 for v in b["skills"].values())
    assert b["phrases"]["GREETING"] == GREETING and b["models"]["llm"]["provider"] == kinds["llm"]["id"]
    assert b["knobs"]["red_flag_mode"] == get_settings().red_flag_mode
    # .env model defaults are written into the release: the agent doesn't depend on .env any more
    assert b["models"]["llm"]["settings"]["model"] == get_settings().groq_llm_model
    assert b["models"]["tts"]["settings"]["voice_ar"] == get_settings().groq_tts_voice_ar
    route = (await store.routes())[0]
    assert {k: route[k] for k in ("workspace_id", "pattern", "agent_id", "priority")} == {
        "workspace_id": WORKSPACE, "pattern": "*", "agent_id": "hmg-care", "priority": 0}
    assert route["label"] is None and route["created_at"]
    # idempotent: nothing new on the next start
    again = await seed(store, get_settings())
    assert not again["workspace_created"] and "agent_created" not in again and not again.get("releases")
    assert len(await store.releases("hmg-care")) == 1


async def test_seed_splits_the_legacy_provider_config():
    legacy = {"llm": {"provider": "groq", "settings": {"api_key": "sk-legacy", "model": "openai/gpt-oss-20b"}},
              "runtime": {"voice_end_silence_ms": 700, "not_a_knob": 1}}
    store = LegacyStore(legacy)
    await seed(store, get_settings())
    llm = next(p for p in await store.providers() if p["kind"] == "llm")
    assert llm["settings"] == {"api_key": "sk-legacy"}                     # connection → provider record
    b = (await published(store))["bundle"]
    assert b["models"]["llm"]["settings"] == {"model": "openai/gpt-oss-20b"}   # choice → the agent's release
    assert b["knobs"]["voice_end_silence_ms"] == 700 and "not_a_knob" not in b["knobs"]


async def test_repo_sync_imports_changed_skills_without_clobbering_console_edits(monkeypatch):
    store = MemoryStore()
    await seed(store, get_settings())
    files = bundle_mod.repo_skill_files()
    # a console edit of `complaints` (v2) …
    edited = {**files["complaints"], "SKILL.md": files["complaints"]["SKILL.md"] + "\nconsole edit\n"}
    await store.add_skill_version(WORKSPACE, "complaints", edited, "console", "edit")
    # … survives a restart when the repo copy didn't change
    await seed(store, get_settings())
    assert await store.skill_version(WORKSPACE, "complaints", 2) == edited
    assert len(await store.skill_versions(WORKSPACE, "complaints")) == 2

    # a repo change to `home` becomes home v2 and a new release pinning it
    changed = copy.deepcopy(files)
    changed["home"]["SKILL.md"] += "\nrepo edit\n"
    monkeypatch.setattr(seed_mod, "repo_skill_files", lambda: changed)
    report = await seed(store, get_settings())
    rel = await published(store)
    assert report["releases"][0]["version"] == rel["version"] == 2
    assert rel["bundle"]["skills"]["home"] == 2 and rel["author"] == "repo-sync"
    assert await store.skill_version(WORKSPACE, "home", 2) == changed["home"]


async def test_repo_sync_updates_only_phrases_the_agent_did_not_change(monkeypatch):
    store = MemoryStore()
    await seed(store, get_settings())
    rel = await published(store)
    bundle = rel["bundle"]
    bundle["phrases"]["FALLBACK"] = {"ar": "خطأ مخصص", "en": "Custom error"}          # the agent's own line
    await store.add_release("hmg-care", bundle, "console", "custom fallback")
    new_defaults = {**Phrases.defaults(),
                    "FALLBACK": {"ar": "افتراضي جديد", "en": "New default"},
                    "STILL_WORKING": {"ar": "لحظات", "en": "Almost there."}}
    monkeypatch.setattr(seed_mod.Phrases, "defaults", staticmethod(lambda: new_defaults))
    await seed(store, get_settings())
    phrases = (await published(store))["bundle"]["phrases"]
    assert phrases["FALLBACK"]["en"] == "Custom error"            # kept
    assert phrases["STILL_WORKING"]["en"] == "Almost there."      # followed the new default


# ---------------------------------------------------------------- loading


async def test_loader_builds_the_agent_from_its_release(backend):
    store = MemoryStore()
    await seed(store, get_settings())
    loader = loader_for(store, backend)
    agent = await loader.for_call()
    assert agent.agent_id == "hmg-care" and agent.version == 1 and agent.release_id
    assert set(agent.skills.skills) == set(bundle_mod.repo_skill_files())
    assert "api_book_Appointment" in agent.executor.catalog
    assert agent.providers.config["llm"]["provider"] == "groq"
    assert await loader.for_call() is agent                     # cached per release
    loader.invalidate()
    assert await loader.for_call() is not agent


async def test_a_call_keeps_its_agent_after_a_publish(backend):
    store = MemoryStore()
    await seed(store, get_settings())
    loader = loader_for(store, backend)
    first = await loader.for_call()
    bundle = (await published(store))["bundle"]
    bundle["knobs"]["voice_end_silence_ms"] = 900
    await store.add_release("hmg-care", bundle, "console", "slower turn end")
    loader.invalidate()
    second = await loader.for_call()
    assert first.settings.voice_end_silence_ms == get_settings().voice_end_silence_ms   # frozen for its call
    assert second.settings.voice_end_silence_ms == 900 and second.version == 2
    assert get_settings().voice_end_silence_ms != 900                                    # process settings untouched


async def test_second_agent_with_its_own_phrases_knobs_and_route(backend):
    store = MemoryStore()
    await seed(store, get_settings())
    b = copy.deepcopy((await published(store))["bundle"])
    b["agent"]["name"] = "Clinic FAQ"
    b["phrases"] = {**b["phrases"], "GREETING": {"ar": "هلا بك في العيادة", "en": "Welcome to the clinic line."}}
    b["knobs"]["red_flag_mode"] = "stop"
    b["models"]["llm"]["settings"] = {"model": "openai/gpt-oss-20b"}
    await store.put_agent({"id": "faq", "workspace_id": WORKSPACE, "name": "Clinic FAQ"})
    await store.add_release("faq", b, "console", "v1")
    await store.put_route(WORKSPACE, "920000*", "faq", priority=10)
    await store.put_route(WORKSPACE, "0551112222", "faq", priority=5)
    loader = loader_for(store, backend)

    assert (await loader.for_call(number="9200001234")).agent_id == "faq"       # prefix route
    assert (await loader.for_call(number="0551112222")).agent_id == "faq"       # exact number
    assert (await loader.for_call(number="0500000000")).agent_id == "hmg-care"  # default '*'
    faq = await loader.for_call(agent_id="faq")
    assert faq.settings.red_flag_mode == "stop" and faq.providers.llm.settings.model == "openai/gpt-oss-20b"
    with pytest.raises(LookupError):
        await loader.for_call(agent_id="nope")

    # the harness speaks the agent's own greeting
    out = Output()
    agent = Agent(Session(call_id="c-faq"), faq.executor, ScriptedLLM(), faq.skills, out, EventBus().bind(),
                  settings=faq.settings)
    await agent.start()
    assert out.said == ["هلا بك في العيادة"]


async def test_repo_agent_without_a_database(backend):
    loader = loader_for(None, backend)
    agent = await loader.for_call()
    assert agent.release_id is None and agent.version is None
    assert set(agent.skills.skills) == set(bundle_mod.repo_skill_files())
    assert agent.inline_bundle()["skill_files"] == agent.skill_files


# ---------------------------------------------------------------- helpers


def test_knobs_overlay_and_coercion():
    s = agent_settings(get_settings(), {"voice_barge_in_confirm": "false", "voice_level_gate_db": "9",
                                        "unknown": 1})
    assert s.voice_barge_in_confirm is False and s.voice_level_gate_db == 9.0
    assert bundle_mod.knob_errors({"voice_end_silence_ms": "x", "nope": 1}) == [
        "knobs: voice_end_silence_ms must be int", "knobs: unknown setting nope"]


def test_connection_settings_stay_on_the_provider():
    conn, choice = split_settings({"api_key": "k", "base_url": "u", "model": "m", "voice_ar": "aisha"})
    assert conn == {"api_key": "k", "base_url": "u"} and choice == {"model": "m", "voice_ar": "aisha"}


async def test_mcp_config_reads_the_token_by_name():
    cfg = await mcp_config([{"name": "hmg_tools", "url": "https://x/mcp", "auth": {"secret": "MCP_AUTH_TOKEN"}},
                            {"name": "off", "url": "https://y", "enabled": False}], get_settings())
    assert list(cfg) == ["hmg_tools"]
    assert cfg["hmg_tools"]["headers"]["Authorization"].startswith("Bearer ")


def test_repo_bundle_is_valid():
    assert bundle_mod.validate(repo_bundle(get_settings())) == []
