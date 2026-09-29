"""Prefetch rules: start slow lookups the agent will very likely need next, while the caller is talking.

Measured live: clinics for a hospital ~2 s, doctor slots 1.7–3.1 s. Prefetched results land in the
executor cache (or are joined while in flight), so the LLM's own call returns immediately.
"""

from typing import TYPE_CHECKING

from runtime.providers import ToolCall
from runtime.text.arabic import normalize
from runtime.tools import ToolResult

if TYPE_CHECKING:
    from .engine import Agent

CLINICS = "mssql_get_clinics_for_project"
NEAREST_SLOTS = "mssql_get_TopFive_nearestClinic_have_doctorSlots"
DATE_SLOTS = "mssql_get_TopFive_availableDoctors_with_slots_byDate"


def after_tool(agent: "Agent", call: ToolCall, result: ToolResult) -> None:
    if not result.ok:
        return
    data = result.data if isinstance(result.data, dict) else {}
    if call.name == "find_hospital_by_name" and data.get("status") == "match":
        _clinics(agent, data["hospital"]["project_id"])
    elif call.name == "find_hospital_by_name" and data.get("status") == "ambiguous":
        for c in data.get("candidates", [])[:2]:
            _clinics(agent, c["project_id"])
    elif call.name == "mssql_get_Projects_from_Location":
        for p in (data.get("projects") or [])[:3]:
            if p.get("ProjectID") is not None:
                _clinics(agent, int(p["ProjectID"]))


def after_text(agent: "Agent", text: str) -> None:
    """When the agent names a clinic (suggesting it), fetch that clinic's nearest slots."""
    s = agent.s
    clinics: dict[int, str] = s.memory.get("clinics") or {}
    project_id = s.slots.get("project_id")
    if not clinics or project_id is None:
        return
    said = normalize(text)
    mentioned = [cid for cid, name in clinics.items() if len(normalize(name)) >= 4 and normalize(name) in said]
    day = s.memory.get("requested_date")
    for cid in mentioned[:2]:
        args = {"projectId": int(project_id), "clinicId": cid}
        agent.executor.prefetch(DATE_SLOTS if day else NEAREST_SLOTS, {**args, "date": day} if day else args,
                                agent._ctx(), agent.ev)


def _clinics(agent: "Agent", project_id: int) -> None:
    agent.executor.prefetch(CLINICS, {"projectId": int(project_id)}, agent._ctx(), agent.ev)

