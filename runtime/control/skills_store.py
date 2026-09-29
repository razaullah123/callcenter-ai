"""Skill editing with version history. Files on disk (skills/<name>/SKILL.md, flow.yaml) are the live
version; every save is validated against the tool catalog first, stored as a new version, then written and
hot-reloaded (new turns use it immediately)."""

import json
from typing import Any

import yaml

from runtime.data.db import get_pool
from runtime.skills import FileSkillSet, Flow, parse_skill_md

FILES = ("SKILL.md", "flow.yaml")


def read_files(skills: FileSkillSet, name: str) -> dict[str, str] | None:
    folder = skills.directory / name
    if not (folder / "SKILL.md").exists():
        return None
    return {f: (folder / f).read_text(encoding="utf-8") for f in FILES if (folder / f).exists()}


def validate(skills: FileSkillSet, name: str, files: dict[str, str]) -> list[str]:
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
            flow = Flow.parse(files["flow.yaml"])
            referenced |= flow.all_tools()
            ids = [s.id for s in flow.steps]
            if len(ids) != len(set(ids)):
                errors.append("flow.yaml: step ids must be unique")
            if not isinstance(cfg.get("steps", []), list):
                errors.append("flow.yaml: steps must be a list")
        except Exception as e:
            errors.append(f"flow.yaml: {e}")
    if unknown := referenced - known:
        errors.append(f"unknown tools: {', '.join(sorted(unknown))}")
    return errors


async def save(skills: FileSkillSet, name: str, files: dict[str, str], author: str, note: str) -> int:
    pool = await get_pool()
    async with pool.acquire() as c, c.transaction():
        current = read_files(skills, name)
        if current and not await c.fetchval("SELECT 1 FROM skill_versions WHERE skill = $1", name):
            # first edit: keep what was on disk as version 1
            await c.execute("INSERT INTO skill_versions (skill, version, author, note, files) "
                            "VALUES ($1, 1, 'system', 'initial (from disk)', $2::jsonb)", name, json.dumps(current))
        version = (await c.fetchval("SELECT coalesce(max(version), 0) FROM skill_versions WHERE skill = $1",
                                    name)) + 1
        await c.execute("INSERT INTO skill_versions (skill, version, author, note, files) VALUES ($1, $2, $3, $4, "
                        "$5::jsonb)", name, version, author, note, json.dumps(files))
    folder = skills.directory / name
    folder.mkdir(parents=True, exist_ok=True)
    for f in FILES:
        path = folder / f
        if files.get(f, "").strip():
            path.write_text(files[f], encoding="utf-8")
        elif path.exists() and f == "flow.yaml":
            path.unlink()
    skills.reload()
    return version


async def versions(name: str) -> list[dict[str, Any]]:
    pool = await get_pool()
    async with pool.acquire() as c:
        rows = await c.fetch("SELECT version, created_at, author, note FROM skill_versions WHERE skill = $1 "
                             "ORDER BY version DESC", name)
    return [dict(r) for r in rows]


async def get_version(name: str, version: int) -> dict[str, str] | None:
    pool = await get_pool()
    async with pool.acquire() as c:
        files = await c.fetchval("SELECT files FROM skill_versions WHERE skill = $1 AND version = $2", name, version)
    return (files if isinstance(files, dict) else json.loads(files)) if files else None
