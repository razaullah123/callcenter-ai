"""Builds the LLM request for a turn: system prompt + trimmed history.

The system prompt starts with the static persona (stable prefix → cache friendly) and ends with the
dynamic call facts (date, language, gender, verification, choices so far, active skill instructions).
"""

import json
from typing import Any

from runtime.providers.base import Message

from .nlu.dates import describe, today_riyadh
from .prompts import GENDER_DIRECTIVE, Phrases
from .session import VERIFY_SKILL, Session
from .skills import SkillSet

MAX_HISTORY = 24   # messages


def system_prompt(session: Session, skills: SkillSet) -> str:
    lang = session.language.language
    ph: Phrases = getattr(skills, "phrases", None) or Phrases()
    facts: list[str] = [
        f"Today is {describe(today_riyadh(), lang)} (Asia/Riyadh).",
        f"Call language: {'Arabic (Najdi dialect)' if lang == 'ar' else 'English'} — reply only in this language.",
    ]
    if directive := GENDER_DIRECTIVE.get((lang, session.gender.known)):
        facts.append(directive)
    a = session.auth
    if not a.required:
        pass                                   # an agent that doesn't verify callers: nothing to say about it
    elif a.verified:
        facts.append(f"Caller is VERIFIED. Name: {a.full_name or a.first_name or 'unknown'}"
                     + ("" if a.identity_confirmed else " (not yet confirmed they are the patient)") + ".")
    else:
        facts.append(f"Caller is NOT verified yet (stage: {a.stage}). Verify before helping with anything else.")
    if session.pending_intent and not a.verified:
        facts.append(f"The caller's request (handle it after verification): \"{session.pending_intent}\"")
    if session.slots.get("branch"):
        facts.append(f"The caller dialed the {session.slots['branch']} branch directly: use it as the hospital "
                     "unless they ask for another one.")
    visible_slots = {k: v for k, v in session.slots.items() if k not in ("appointment",)}
    # the hospital's name in the call language — the model turned "مستشفى العليا" into "Al-Ula Hospital"
    name_en = visible_slots.pop("project_name_en", None)
    if lang == "en" and name_en:
        visible_slots["project_name"] = name_en
    if visible_slots:
        facts.append("Choices so far: " + json.dumps(visible_slots, ensure_ascii=False, default=str))
    if session.pending_action:
        facts.append(f"Waiting for the caller to confirm: {session.pending_action.tool} "
                     f"{json.dumps(session.pending_action.args, ensure_ascii=False)}")
    if a.verified and not session.flow_skill:          # one flow runs the call: no switching between services
        routable = skills.routable()
        facts.append("Available services (use switch_skill to change): " +
                     "; ".join(f"{k}: {v}" for k, v in routable.items()) + f". Current: {session.active_skill}.")
    persona = getattr(skills, "persona", lambda _l: None)(lang) or ph.PERSONA[lang]
    parts = [persona, "\n## Call facts\n" + "\n".join(f"- {f}" for f in facts)]
    skill = session.flow_skill or (VERIFY_SKILL if not a.verified else session.active_skill)
    instr = skills.instructions(skill, session) or (ph.AUTH_STEPS.get(a.stage, "") if not a.verified else "")
    if a.required and a.verified and not a.identity_confirmed:
        name = session.memory.get("name_ar") if lang == "ar" else None
        question = ph.IDENTITY_QUESTION[lang].format(name=name or a.full_name or a.first_name or "")
        skill, instr = "confirm_identity", ph.IDENTITY_STEP.format(question=question)
    if instr:
        parts.append(f"\n## Current task: {skill}\n{instr}")
    return "\n".join(parts)


def trimmed_history(history: list[Message], limit: int = MAX_HISTORY) -> list[Message]:
    """Last `limit` messages, never starting on a tool result (tool calls and results stay paired)."""
    if len(history) <= limit:
        return list(history)
    start = len(history) - limit
    while start < len(history) and history[start].get("role") == "tool":
        start += 1
    return history[start:]


def build_messages(session: Session, skills: SkillSet) -> list[Message]:
    return [{"role": "system", "content": system_prompt(session, skills)}, *trimmed_history(session.history)]


def tool_call_message(text: str, calls: list[Any]) -> Message:
    return {"role": "assistant", "content": text or None, "tool_calls": [
        {"id": c.id, "type": "function",
         "function": {"name": c.name, "arguments": c.raw_arguments or json.dumps(c.arguments, ensure_ascii=False)}}
        for c in calls]}
