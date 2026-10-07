"""Agent-to-agent hand-off (the flow's "transfer agent" node): what the next agent inherits from the call."""

import copy
from dataclasses import dataclass

from .session import Session
from .variables import build_custom

MAX_AGENT_TRANSFERS = 3        # per call: stops two agents passing a caller back and forth


@dataclass
class AgentTransfer:
    agent_id: str
    history: bool = False       # hand over the conversation so far
    variables: bool = False     # hand over the values collected so far


def carry_over(old: Session, agent_name: str, req: AgentTransfer, *, agent_id: str = "",
               declared: dict | None = None) -> Session:
    """The session the next agent starts with: the same call, caller number, language and gender; the conversation and
    the collected values only when the node asked for them."""
    new = Session(call_id=old.call_id, ani=old.ani, agent_name=agent_name, agent_number=old.agent_number,
                  direction=old.direction, agent_id=agent_id, params=dict(old.params))
    try:                                       # the same params, read against the next agent's declared variables
        new.custom = build_custom(declared or {}, new.params)
    except ValueError:
        new.custom = build_custom(declared or {}, None)
    new.language, new.gender = old.language, old.gender
    new.web_tools = set(old.web_tools)                 # the visitor's page is still the same page
    new.supervisor_notes = list(old.supervisor_notes)  # so are the live instructions given to this call
    new.turn_id = old.turn_id
    new.memory["agent_transfers"] = old.memory.get("agent_transfers", 0) + 1
    if req.history:
        new.history = copy.deepcopy(old.history)
    if req.variables:
        new.slots = {k: v for k, v in old.slots.items() if not k.startswith("_")}
    return new
