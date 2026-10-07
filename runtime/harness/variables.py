"""Variables of a call (Hamsa's Variable System): the system variables the platform provides, the custom variables an agent
declares (with defaults, overridable by `params` when a call starts), the naming rules, and the checks.

    system   built in, always available            {{ call_id }} {{ current_time }} {{ user_number }} …
    custom   declared on the agent (name, type, default) — values may come in as `params` when the call starts
    extracted / static   created by flow nodes while the call runs (see runtime.skills.graph)
"""

import json
import re
from typing import Any

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,49}$")
CUSTOM_TYPES = ("string", "number", "boolean", "array", "object")
MAX_PARAMS_JSON = 50_000

# (group, name, description) — the variables the platform fills in; the console lists them from here.
SYSTEM_VARIABLES: list[tuple[str, str, str]] = [
    ("Time", "current_time", "Current time, HH:MM (Riyadh)"),
    ("Time", "current_date", "Current date, YYYY-MM-DD (Riyadh)"),
    ("Time", "current_datetime", "Current date and time, YYYY-MM-DDTHH:MM:SS (Riyadh)"),
    ("Time", "current_timestamp", "Unix timestamp in milliseconds"),
    ("Time", "current_day", "Day of the month"),
    ("Time", "current_month", "Month number (1-12)"),
    ("Time", "current_year", "Four-digit year"),
    ("Time", "current_weekday", "Full weekday name"),
    ("Call", "call_id", "Unique identifier of this call"),
    ("Call", "call_type", "phone, web or chat"),
    ("Call", "direction", "inbound or outbound"),
    ("Call", "call_start_time", "ISO timestamp when the call began"),
    ("Call", "call_lang", "The language the call is in now (ar / en)"),
    ("User", "user_number", "The caller's phone number"),
    ("User", "user_number_area_code", "Area code of the caller's number (Saudi numbers)"),
    ("Agent", "agent_number", "The number or extension that was called"),
    ("Agent", "agent_name", "The agent's name"),
    ("Agent", "agent_id", "The agent's identifier"),
]
# older names flows already use (Hamsa's flow-side names): still provided, not shown as new
LEGACY_NAMES = ("userNumber", "callParams")
RESERVED = {name for _, name, _ in SYSTEM_VARIABLES} | set(LEGACY_NAMES)


def name_problem(name: str) -> str | None:
    """Why `name` can't be a variable name (Hamsa's rules: snake_case, 1-50 characters, starting with a lowercase letter,
    not a system variable), or None."""
    if not isinstance(name, str) or not NAME_RE.match(name):
        return "must be snake_case — a lowercase letter, then lowercase letters, digits or underscores, 1-50 characters"
    if name in RESERVED:
        return "is a system variable name"
    return None


def _fits(kind: str, value: Any) -> bool:
    return {"string": isinstance(value, str), "boolean": isinstance(value, bool), "array": isinstance(value, list),
            "object": isinstance(value, dict),
            "number": isinstance(value, (int, float)) and not isinstance(value, bool)}.get(kind, True)


def variable_errors(declared: dict[str, Any]) -> list[str]:
    """Problems in an agent's custom variables: {name: {"type", "default", "description"}}."""
    out = []
    if not isinstance(declared, dict):
        return ["variables must be an object"]
    for name, spec in declared.items():
        if problem := name_problem(name):
            out.append(f"variables.{name}: the name {problem}")
            continue
        spec = spec if isinstance(spec, dict) else {}
        kind = spec.get("type", "string")
        if kind not in CUSTOM_TYPES:
            out.append(f"variables.{name}: type must be one of {', '.join(CUSTOM_TYPES)}")
        elif spec.get("default") is not None and not _fits(kind, spec["default"]):
            out.append(f"variables.{name}: the default doesn't fit the type {kind}")
    return out


def declared_variables(bundle: dict[str, Any] | None) -> dict[str, Any]:
    return (bundle or {}).get("variables") or {}


def _coerce(kind: str, value: Any) -> Any:
    """A param as the declared type (callers often send text): numbers, booleans, JSON arrays / objects."""
    if _fits(kind, value):
        return value
    try:
        if kind == "number" and isinstance(value, str):
            f = float(value)
            return int(f) if f.is_integer() else f
        if kind == "boolean" and isinstance(value, str) and value.strip().lower() in ("true", "false", "yes", "no", "1", "0"):
            return value.strip().lower() in ("true", "yes", "1")
        if kind in ("array", "object") and isinstance(value, str):
            parsed = json.loads(value)
            if _fits(kind, parsed):
                return parsed
        if kind == "string" and isinstance(value, (int, float, bool)):
            return str(value)
    except ValueError:
        pass
    raise ValueError(f"expected {kind}")


def build_custom(declared: dict[str, Any], params: dict[str, Any] | None) -> dict[str, Any]:
    """The call's custom variables: every declared one at its default, `params` from the call start on top (any valid
    name; a declared one must fit its type). Raises ValueError with a message the client can show."""
    out: dict[str, Any] = {}
    for name, spec in declared.items():
        spec = spec if isinstance(spec, dict) else {}
        default = spec.get("default")
        out[name] = "" if default is None else default
    if params is not None and not isinstance(params, dict):
        raise ValueError("params must be an object")
    for name, value in (params or {}).items():
        if problem := name_problem(name):
            raise ValueError(f"param {name!r}: the name {problem}")
        spec = declared.get(name)
        if isinstance(spec, dict):
            try:
                value = _coerce(spec.get("type", "string"), value)
            except ValueError as e:
                raise ValueError(f"param {name!r}: {e}") from None
        out[name] = value
    try:
        if len(json.dumps(out, ensure_ascii=False, default=str)) > MAX_PARAMS_JSON:
            raise ValueError("params are too large")
    except TypeError:
        raise ValueError("params must be plain JSON values") from None
    return out
