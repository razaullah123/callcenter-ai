"""What the harness needs from skills. Phase 5 provides the full implementation (SKILL.md + flow.yaml
+ step machine); `SimpleSkillSet` is a minimal version built from the tool catalog.
"""

from dataclasses import dataclass, field
from typing import Protocol

from runtime.tools import Catalog

from .session import Session


class SkillSet(Protocol):
    def routable(self) -> dict[str, str]:
        """Skills the router may switch to: name → one-line description."""
        ...

    def instructions(self, skill: str, session: Session) -> str:
        """Instructions for the active skill (may depend on the current step)."""
        ...

    def tools(self, skill: str, session: Session) -> set[str]:
        """Tool names available to the LLM in the current skill / step."""
        ...


@dataclass
class SimpleSkillSet:
    catalog: Catalog
    descriptions: dict[str, str] = field(default_factory=dict)
    texts: dict[str, str] = field(default_factory=dict)

    def routable(self) -> dict[str, str]:
        skills = {t.skill for t in self.catalog.tools.values()} - {"authenticate"}
        return {s: self.descriptions.get(s, s.replace("_", " ")) for s in sorted(skills)}

    def instructions(self, skill: str, session: Session) -> str:
        return self.texts.get(skill, "")

    def tools(self, skill: str, session: Session) -> set[str]:
        return {t.name for t in self.catalog.for_skill(skill)}
