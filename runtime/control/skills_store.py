"""Skill editing on the platform: every save is validated against the agent's tool catalog, stored as a new skill
version, and published as a new release of each agent that uses the skill (new calls use it; calls in progress
keep the version they started with)."""

from typing import Any

import yaml

from runtime.platform import WORKSPACE, current_project
from runtime.platform.loader import skill_ref
from runtime.skills import Graph, SkillSet, parse_skill_md, read_skill_dir

FILES = ("SKILL.md", "flow.yaml")


def validate(skills: SkillSet, name: str, files: dict[str, str]) -> list[str]:
    errors = []
    if "SKILL.md" not in files or not files["SKILL.md"].strip():
        return ["SKILL.md is required"]
    try:
        skill = parse_skill_md(files["SKILL.md"], name)
    except Exception as e:
        return [f"SKILL.md: {e}"]
    known = set(skills.catalog.tools)
    referenced = set(skill.extra_tools) | set(skill.hidden_tools)
    if files.get("flow.yaml", "").strip():
        try:
            cfg = yaml.safe_load(files["flow.yaml"]) or {}
            if not isinstance(cfg.get("steps", []), list) or not isinstance(cfg.get("nodes", []), list):
                errors.append("flow.yaml: steps / nodes must be a list")
            ids = [x.get("id") for x in (cfg.get("nodes") or cfg.get("steps") or []) if isinstance(x, dict)]
            if len(ids) != len(set(ids)):
                errors.append("flow.yaml: node / step ids must be unique")
            flow = Graph.parse(files["flow.yaml"])
            referenced |= flow.all_tools()
            errors += [f"flow.yaml: {e}" for e in flow.errors()]
        except Exception as e:
            errors.append(f"flow.yaml: {e}")
    if unknown := referenced - known:
        errors.append(f"unknown tools: {', '.join(sorted(unknown))}")
    from runtime.tools.hooks import unknown_hooks
    if missing := unknown_hooks(turn=set(skill.turn_hooks)):
        errors.append(f"unknown turn hooks: {', '.join(missing)}")
    return errors


async def read_files(rt, name: str) -> dict[str, str] | None:
    """The version the default agent runs (the repo copy without a database)."""
    bundle = rt.agent.bundle or {}
    if rt.platform is not None and name in (bundle.get("skills") or {}):
        lib, version = skill_ref(name, bundle["skills"][name])
        return await rt.platform.skill_version(current_project(), lib, version)
    if name in (bundle.get("skill_files") or {}):
        return bundle["skill_files"][name]
    return read_skill_dir().get(name)


async def save(rt, name: str, files: dict[str, str], author: str, note: str) -> dict[str, Any]:
    """New skill version + a new published release of every agent using it (a new skill joins the default agent)."""
    store = rt.platform
    clean = {f: files[f] for f in FILES if files.get(f, "").strip()}
    version = await store.add_skill_version(current_project(), name, clean, author, note)
    releases = []
    for agent in await store.agents(current_project()):
        if not agent.get("published_release_id"):
            continue
        rel = await store.release(agent["published_release_id"])
        bundle = rel["bundle"]
        refs = bundle.setdefault("skills", {})
        keys = [k for k, ref in refs.items() if skill_ref(k, ref)[0] == name]      # plain or aliased uses
        if not keys and agent["id"] == rt.agent.agent_id:
            keys = [name]
        for k in keys:
            refs[k] = {"skill": name, "version": version} if isinstance(refs.get(k), dict) else version
        if keys:
            r = await store.add_release(agent["id"], bundle, author, note or f"{name} v{version}")
            releases.append({"agent": agent["id"], **r})
    from runtime.control.api import changed
    await changed(rt, "skill", name=name)
    return {"version": version, "releases": releases}


async def versions(rt, name: str) -> list[dict[str, Any]]:
    return await rt.platform.skill_versions(current_project(), name) if rt.platform is not None else []


async def get_version(rt, name: str, version: int) -> dict[str, str] | None:
    return await rt.platform.skill_version(current_project(), name, version) if rt.platform is not None else None
