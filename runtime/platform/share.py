"""Public page and embed widget of an agent (Hamsa's "Publish"): one link per agent that anyone can open and talk to
without signing in, and the settings behind it — appearance, embed widget, limits.

A share is a row (agent_id, token, settings). Deleting the row unpublishes: the page stops loading, new calls are refused
and the calls still running on the link are ended. The link only ever starts the agent's *published release*.
"""

import re
import secrets
import time
from collections import defaultdict, deque
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from runtime.harness.variables import name_problem

HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
ORIGIN = re.compile(r"^https?://[A-Za-z0-9*.-]+(:\d{1,5})?$")


def _hex(v: str) -> str:
    if not HEX.match(v):
        raise ValueError("must be a colour like #6366f1")
    return v


class EmbedSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    position: Literal["bottom-left", "bottom-center", "bottom-right"] = "bottom-right"
    size: Literal["sm", "md", "lg"] = "md"
    label: str = Field("", max_length=40)             # empty: a round icon bubble; text: a pill button
    auto_start: bool = False                          # start the call as soon as the widget opens
    launcher: Literal["auto", "always", "never"] = "auto"
    color: str = "#6366f1"

    _color = field_validator("color")(_hex)


class LimitSettings(BaseModel):
    """What a visitor can spend: every public call costs speech-recognition, language-model and voice credits."""
    model_config = ConfigDict(extra="forbid")
    max_minutes: float = Field(5, ge=1, le=60)        # one call's longest length
    max_concurrent: int = Field(5, ge=1, le=100)      # calls at the same time on this link
    per_ip_per_hour: int = Field(10, ge=1, le=1000)   # calls one visitor (IP address) may start per hour
    allowed_origins: list[str] = Field(default_factory=list, max_length=20)   # sites that may embed the page; empty: any
    allowed_params: list[str] = Field(default_factory=list, max_length=20)    # custom variables the page URL may set

    @field_validator("allowed_origins")
    @classmethod
    def _origins(cls, v: list[str]) -> list[str]:
        out = [o.strip().rstrip("/") for o in v if o.strip()]
        for o in out:
            if not ORIGIN.match(o):
                raise ValueError(f"{o!r} must look like https://example.com")
        return out

    @field_validator("allowed_params")
    @classmethod
    def _params(cls, v: list[str]) -> list[str]:
        for name in v:
            if problem := name_problem(name):
                raise ValueError(f"{name!r}: the name {problem}")
        return v


class ShareSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: str = Field("", max_length=60)       # under the agent name on the page
    tagline: str = Field("", max_length=60)           # under the title before the call starts
    show_name: bool = False
    show_transcript: bool = True
    theme: Literal["light", "dark"] = "dark"
    theme_switcher: bool = False                      # visitors may flip light / dark
    visualizer: Literal["orb", "wave", "aura"] = "orb"
    gradient: list[str] = Field(default_factory=lambda: ["#CADCFC", "#A0B9D1"], min_length=2, max_length=2)
    bg_dark: str = "#0A0A0A"
    bg_light: str = "#FAFAFA"
    embed: EmbedSettings = Field(default_factory=EmbedSettings)
    limits: LimitSettings = Field(default_factory=LimitSettings)

    @field_validator("gradient")
    @classmethod
    def _gradient(cls, v: list[str]) -> list[str]:
        return [_hex(c) for c in v]

    _bg_dark = field_validator("bg_dark")(_hex)
    _bg_light = field_validator("bg_light")(_hex)


def new_token() -> str:
    return secrets.token_urlsafe(24)


def public_config(settings: dict[str, Any], agent: dict[str, Any]) -> dict[str, Any]:
    """What the public page and the embed script may know: appearance, embed options, the params the URL may set — never
    the limits' details or anything about the agent beyond a name the owner chose to show."""
    s = ShareSettings.model_validate(settings or {})
    return {"name": agent.get("name", "") if s.show_name else "", "description": s.description, "tagline": s.tagline,
            "show_transcript": s.show_transcript, "theme": s.theme, "theme_switcher": s.theme_switcher,
            "visualizer": s.visualizer, "gradient": s.gradient, "bg_dark": s.bg_dark, "bg_light": s.bg_light,
            "embed": s.embed.model_dump(), "allowed_params": s.limits.allowed_params,
            "max_minutes": s.limits.max_minutes}


class PublicLimiter:
    """Who may start a call on a public link now: a ceiling on calls at the same time per link, and on calls per hour per visitor.
    In memory, per process (a second worker counts on its own)."""

    def __init__(self) -> None:
        self.active: dict[str, dict[int, Any]] = defaultdict(dict)       # token → {id: call}
        self.hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._next = 0

    def admit(self, token: str, ip: str, limits: LimitSettings, now: float | None = None) -> str | None:
        """None: allowed (and counted); else why not: "busy" or "rate"."""
        now = time.time() if now is None else now
        window = self.hits[(token, ip)]
        while window and now - window[0] > 3600:
            window.popleft()
        if len(self.active[token]) >= limits.max_concurrent:
            return "busy"
        if len(window) >= limits.per_ip_per_hour:
            return "rate"
        window.append(now)
        if len(self.hits) > 5000:                                       # forget visitors who stopped calling
            for key in [k for k, w in self.hits.items() if not w or now - w[-1] > 3600]:
                self.hits.pop(key, None)
        return None

    def join(self, token: str, call: Any) -> int:
        self._next += 1
        self.active[token][self._next] = call
        return self._next

    def leave(self, token: str, handle: int) -> None:
        self.active.get(token, {}).pop(handle, None)
        if token in self.active and not self.active[token]:
            self.active.pop(token, None)

    def calls_of(self, token: str) -> list[Any]:
        return list(self.active.get(token, {}).values())


LIMITER = PublicLimiter()

REFUSALS = {"busy": "All lines are busy right now — please try again in a moment.",
            "rate": "Too many calls from your connection — please try again later."}
