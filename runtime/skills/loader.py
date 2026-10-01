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

from .graph import DONE, Graph, Node

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
    turn_hooks: list[str] = field(default_factory=list)    # named turn hooks (runtime.tools.hooks, from packs)
    body: dict[str, str] = field(default_factory=dict)   # "ar" / "en" / "*"
    flow: Graph | None = None
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
                 turn_hooks=list(meta.get("turn_hooks", [])),
                 body=sections)


SKILL_FILES = ("SKILL.md", "flow.yaml")


def read_skill_dir(directory: Path = SKILLS_DIR) -> dict[str, dict[str, str]]:
    """skills/<name>/{SKILL.md, flow.yaml} → {name: {file: text}} (the repo copy: seed / export)."""
    out = {}
    for md in sorted(directory.glob("*/SKILL.md")):
        out[md.parent.name] = {f: (md.parent / f).read_text(encoding="utf-8")
                               for f in SKILL_FILES if (md.parent / f).exists()}
    return out


def call_facts(session: Any) -> dict[str, Any]:
    """What edges can test besides the collected values: the harness's view of the call right now."""
    a = session.auth
    parsed = session.memory.get("parsed") or {}
    return {"verified": a.verified, "identity_confirmed": a.identity_confirmed,
            "mobile_heard": bool(parsed.get("mobile")), "code_heard": bool(parsed.get("code")),
            "files_found": len(a.candidates) if a.lookup_attempts else None,
            "otp_exhausted": a.otp_attempts >= 3, "awaiting_confirmation": session.pending_action is not None,
            "_turn": session.turn_id}


class SkillSet:
    """An agent's skills, built from their files (a release's frozen skill versions, or the repo folder)."""

    def __init__(self, catalog: Catalog, files: dict[str, dict[str, str]], phrases: Any = None) -> None:
        self.catalog = catalog
        self.phrases = phrases          # the agent's fixed lines (harness.prompts.Phrases); None → defaults
        self.skills: dict[str, Skill] = {}
        self._load(files)

    def _load(self, files: dict[str, dict[str, str]]) -> None:
        skills = {}
        for folder, texts in sorted(files.items()):
            skill = parse_skill_md(texts["SKILL.md"], folder)
            if (flow := texts.get("flow.yaml", "")).strip():
                skill.flow = Graph.parse(flow)          # step flows are converted to graphs
                if errors := skill.flow.errors():
                    raise ValueError(f"skill {skill.name}: flow: {'; '.join(errors)}")
            skills[skill.name] = skill
        self.skills = skills
        self._validate()

    def _validate(self) -> None:
        from runtime.tools.hooks import unknown_hooks
        known = set(self.catalog.tools)
        for s in self.skills.values():
            referenced = (s.flow.all_tools() if s.flow else set()) | set(s.extra_tools) | set(s.hidden_tools)
            if unknown := referenced - known:
                raise ValueError(f"skill {s.name}: references unknown tools {sorted(unknown)}")
            if missing := unknown_hooks(turn=set(s.turn_hooks)):
                raise ValueError(f"skill {s.name}: unknown turn hooks {missing}")

    # ---------------- SkillSet protocol ----------------

    def turn_hook_names(self, skill: str) -> list[str]:
        s = self.skills.get(skill)
        return list(s.turn_hooks) if s else []

    def persona(self, lang: str) -> str | None:
        p = self.skills.get("_persona")
        return p.text(lang) if p else None

    def routable(self) -> dict[str, str]:
        return {s.name: s.description for s in self.skills.values() if s.routable}

    # ---- flow graph (the skill's conversation state lives in session.memory["graph"][skill])

    def graph_state(self, skill: str, session: Any) -> dict[str, Any]:
        return session.memory.setdefault("graph", {}).setdefault(skill, {})

    def node(self, skill: str, session: Any) -> Node | None:
        """The skill's current node, after following every edge whose condition now holds."""
        s = self.skills.get(skill)
        if s is None or s.flow is None:
            return None
        return s.flow.current(self.graph_state(skill, session), session.slots, session.auth.stage,
                              call_facts(session))

    def graph(self, skill: str) -> Graph | None:
        s = self.skills.get(skill)
        return s.flow if s else None

    def instructions(self, skill: str, session: Any) -> str:
        s = self.skills.get(skill)
        if s is None:
            return ""
        lang = session.language.language
        text = s.text(lang)
        node = self.node(skill, session)
        if node is not None and node.id != DONE and node.type == "conversation":
            text += f"\n\n### Current step: {node.id}\n{node.instructions}"
        return text

    def tools(self, skill: str, session: Any) -> set[str]:
        s = self.skills.get(skill)
        if s is None:
            return set()
        if s.flow:
            node = self.node(skill, session)
            return set(s.flow.common_tools) | set(node.tools if node else []) | ({node.tool} if node and node.tool else set())
        return ({t.name for t in self.catalog.for_skill(skill)} | set(s.extra_tools)) - set(s.hidden_tools)

    def current_step(self, skill: str, session: Any) -> str | None:
        node = self.node(skill, session)
        return node.id if node is not None and node.id != DONE else None

    def auto_calls(self, skill: str, session: Any) -> list[tuple[str, dict[str, Any], bool]]:
        s = self.skills.get(skill)
        node = self.node(skill, session)
        return s.flow.auto_calls(node, session.slots, session.memory.get("parsed")) if node is not None else []

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


class FileSkillSet(SkillSet):
    """Skills straight from a folder (tests, offline tools, evals on the repo copy)."""

    def __init__(self, catalog: Catalog, directory: Path = SKILLS_DIR, phrases: Any = None) -> None:
        self.directory = directory
        super().__init__(catalog, read_skill_dir(directory), phrases)

    def reload(self) -> None:
        self._load(read_skill_dir(self.directory))
