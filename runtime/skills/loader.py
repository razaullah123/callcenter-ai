"""Loads skills from skills/<name>/SKILL.md (+ optional flow.yaml) — editable without code changes.

SKILL.md:
    ---
    name: book_appointment
    description: Book a new appointment              # what the router sees (progressive disclosure)
    keywords: [احجز, موعد جديد, book, appointment]    # fast rule-based routing of the caller's first request
    routable: true                                   # false for _persona / home / authenticate
    hidden_tools: [mssql_get_availableDoctors_with_slots_byDate]
    extra_tools: [mssql_get_upcoming_appointment]       # tools from other catalog groups
    status: ready | draft
    ---
    Instructions for the LLM. Optional language sections:
    ## ar
    ...
    ## en
    ...
Only the active skill's instructions (and its current step) enter the prompt.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from runtime.config import ROOT_DIR
from runtime.text.arabic import normalize
from runtime.tools import Catalog

from .flow import Flow

SKILLS_DIR = ROOT_DIR / "skills"
NON_ROUTABLE = {"_persona", "home", "authenticate"}

_FRONT = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)
_LANG_SECTION = re.compile(r"^##\s*(ar|en)\s*$", re.MULTILINE)


@dataclass
class Skill:
    name: str
    description: str = ""
    keywords: list[str] = field(default_factory=list)
    routable: bool = True
    status: str = "ready"
    hidden_tools: list[str] = field(default_factory=list)
    extra_tools: list[str] = field(default_factory=list)   # tools from other catalog groups
    body: dict[str, str] = field(default_factory=dict)   # "ar" / "en" / "*"
    flow: Flow | None = None
    path: Path | None = None

    def text(self, lang: str) -> str:
        return self.body.get(lang) or self.body.get("*", "")


def parse_skill_md(text: str, name: str) -> Skill:
    m = _FRONT.match(text.lstrip("﻿"))
    meta, body = (yaml.safe_load(m.group(1)) or {}, m.group(2)) if m else ({}, text)
    sections: dict[str, str] = {}
    parts = _LANG_SECTION.split(body)
    if parts[0].strip():
        sections["*"] = parts[0].strip()
    for lang, content in zip(parts[1::2], parts[2::2]):
        common = sections.get("*", "")
        sections[lang] = (common + "\n\n" + content.strip()).strip() if common else content.strip()
    return Skill(name=meta.get("name", name), description=meta.get("description", ""),
                 keywords=list(meta.get("keywords", [])),
                 routable=meta.get("routable", name not in NON_ROUTABLE), status=meta.get("status", "ready"),
                 hidden_tools=list(meta.get("hidden_tools", [])), extra_tools=list(meta.get("extra_tools", [])),
                 body=sections)


class FileSkillSet:
    def __init__(self, catalog: Catalog, directory: Path = SKILLS_DIR) -> None:
        self.catalog = catalog
        self.directory = directory
        self.skills: dict[str, Skill] = {}
        self.reload()

    def reload(self) -> None:
        skills = {}
        for md in sorted(self.directory.glob("*/SKILL.md")):
            skill = parse_skill_md(md.read_text(encoding="utf-8"), md.parent.name)
            skill.path = md.parent
            if (flow_file := md.parent / "flow.yaml").exists():
                skill.flow = Flow.load(flow_file)
            skills[skill.name] = skill
        self.skills = skills
        self._validate()

    def _validate(self) -> None:
        known = set(self.catalog.tools)
        for s in self.skills.values():
            referenced = (s.flow.all_tools() if s.flow else set()) | set(s.extra_tools) | set(s.hidden_tools)
            if unknown := referenced - known:
                raise ValueError(f"skill {s.name}: references unknown tools {sorted(unknown)}")

    # ---------------- SkillSet protocol ----------------

    def persona(self, lang: str) -> str | None:
        p = self.skills.get("_persona")
        return p.text(lang) if p else None

    def routable(self) -> dict[str, str]:
        return {s.name: s.description for s in self.skills.values() if s.routable}

    def instructions(self, skill: str, session: Any) -> str:
        s = self.skills.get(skill)
        if s is None:
            return ""
        lang = session.language.language
        text = s.text(lang)
        if s.flow and (step := s.flow.current(session.slots, session.auth.stage)):
            text += f"\n\n### Current step: {step.id}\n{step.instructions}"
        return text

    def tools(self, skill: str, session: Any) -> set[str]:
        s = self.skills.get(skill)
        if s is None:
            return set()
        if s.flow:
            step = s.flow.current(session.slots, session.auth.stage)
            return set(s.flow.common_tools) | set(step.tools if step else [])
        return ({t.name for t in self.catalog.for_skill(skill)} | set(s.extra_tools)) - set(s.hidden_tools)

    def current_step(self, skill: str, session: Any) -> str | None:
        s = self.skills.get(skill)
        if s and s.flow and (step := s.flow.current(session.slots, session.auth.stage)):
            return step.id
        return None

    def auto_calls(self, skill: str, session: Any) -> list[tuple[str, dict[str, Any], bool]]:
        s = self.skills.get(skill)
        return s.flow.auto_calls(session.slots, session.auth.stage, session.memory.get("parsed")) \
            if s and s.flow else []

    def on_tool(self, skill: str, tool: str, args: dict[str, Any], result: Any, ok: bool, session: Any) -> dict:
        s = self.skills.get(skill)
        return s.flow.apply(tool, args, result, ok, session.slots) if s and s.flow else {}

    def classify(self, text: str | None) -> str | None:
        """Route the caller's request by keywords; None if no skill (or more than one) clearly matches."""
        if not text:
            return None
        t = f" {normalize(text)} "
        scores = {}
        for s in self.skills.values():
            if not s.routable:
                continue
            hits = sum(1 for k in s.keywords if f" {normalize(k)} " in t or (len(normalize(k)) > 4 and normalize(k) in t))
            if hits:
                scores[s.name] = hits
        if not scores:
            return None
        best = sorted(scores.items(), key=lambda kv: -kv[1])
        return best[0][0] if len(best) == 1 or best[0][1] > best[1][1] else None
