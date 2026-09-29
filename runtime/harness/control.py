"""Harness control tools — handled by the harness itself, not MCP / local tools."""

from typing import Any

SWITCH_SKILL = "switch_skill"
SELECT_PATIENT = "select_patient"
TRANSFER = "transfer_to_human"
END_CALL = "end_call"
RECORD_ANSWER = "record_answer"


def control_specs(*, verified: bool, routable: dict[str, str], disambiguating: bool,
                  awaiting: list[str] | None = None) -> list[dict[str, Any]]:
    specs = [
        _spec(TRANSFER, "Transfer the caller to a human agent (caller asks for a person, repeated failures, "
                        "or a request you cannot handle).",
              {"reason": {"type": "string", "description": "Short reason for the human agent"}}, ["reason"]),
        _spec(END_CALL, "End the call after saying goodbye, when the caller has nothing else.", {}, []),
    ]
    if verified and routable:
        specs.append(_spec(SWITCH_SKILL, "Switch to the service the caller needs.",
                           {"skill": {"type": "string", "enum": sorted(routable)}}, ["skill"]))
    if awaiting:
        # A yes / no question is waiting and the caller's reply wasn't a plain yes / no: the model reads it.
        specs.append(_spec(RECORD_ANSWER,
                           "Record the caller's answer to the pending yes / no question, as you understand their LAST "
                           "reply (it may be misheard or phrased loosely). Use 'unclear' if you can't tell — then ask "
                           "again. Never answer on the caller's behalf.",
                           {"question": {"type": "string", "enum": awaiting},
                            "answer": {"type": "string", "enum": ["yes", "no", "unclear"]}},
                           ["question", "answer"]))
    if disambiguating:
        specs.append(_spec(SELECT_PATIENT, "Identify the caller among several files on the same mobile number.",
                           {"first_name": {"type": "string", "description": "Caller's first name as they said it"},
                            "date_of_birth": {"type": "string", "description": "Date of birth as the caller said "
                                                                               "it (Gregorian)"}},
                           ["first_name", "date_of_birth"]))
    return specs


def _spec(name: str, description: str, props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "function", "function": {"name": name, "description": description,
                                             "parameters": {"type": "object", "properties": props,
                                                            "required": required}}}


CONTROL_TOOLS = {SWITCH_SKILL, SELECT_PATIENT, TRANSFER, END_CALL, RECORD_ANSWER}
