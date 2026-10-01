"""Builds flows/hmg_call/flow.yaml — the whole HMG call as one flow graph — from the tested step texts of
skills/authenticate and skills/book_appointment (so the wording the evals passed with is reused word for word).

    python flows/hmg_call/build.py
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
auth = {s["id"]: s for s in yaml.safe_load((ROOT / "skills/authenticate/flow.yaml").read_text(encoding="utf-8"))["steps"]}
book_cfg = yaml.safe_load((ROOT / "skills/book_appointment/flow.yaml").read_text(encoding="utf-8"))
book = {s["id"]: s for s in book_cfg["steps"]}
HOSPITAL_TOOLS = ["find_hospital_by_name", "resolve_location"]        # were common_tools of the booking skill


def mobile_text() -> str:
    # the old step also handled "the caller only greeted" — that is the greeting node's job now
    lines = auth["mobile"]["instructions"].splitlines()
    return "\n".join(lines[3:]).strip()


def readback_text() -> str:
    t = book["doctor_and_time"]["instructions"]
    return t[t.index("The system then asks you"):t.index("Use the weekday exactly")].strip()


def P(x: int, y: int) -> dict:
    return {"x": x, "y": y}


nodes = [
    {"id": "greeting_intent", "type": "conversation", "position": P(0, 200), "extract": ["intent"],
     "instructions": (
         "The caller has been greeted. Find out what they need. If they only greeted or it isn't clear yet, ask "
         "exactly: English \"How can I help you today?\" / Arabic \"كيف أقدر أخدمك؟\". Don't ask for the mobile "
         "number here and don't answer the request yourself.")},
    {"id": "other_service", "type": "transfer", "position": P(300, 420),
     "reason": "caller asked for a service that isn't available by phone yet"},
    {"id": "ask_mobile", "type": "conversation", "position": P(300, 200), "tools": ["mssql_get_patient_info"],
     "instructions": ("The caller wants to book. Acknowledge it in a few words and ask for their registered "
                      "mobile number to verify them — e.g. \"أبشر، عشان أحجز لك أحتاج رقم جوالك المسجل\".\n"
                      + mobile_text())},
    {"id": "lookup_patient", "type": "tool", "position": P(600, 200), "tool": "mssql_get_patient_info",
     "args": {"mobileNo": "parsed.mobile"}},
    {"id": "no_file", "type": "conversation", "position": P(900, 420), "extract": ["next_step"],
     "instructions": ("No patient file is registered with this number. Tell the caller to contact the support team to "
                      "register first (they cannot be served without a file), then ask if there is anything else. "
                      "If they give another number, it is looked up.")},
    {"id": "choose_file", "type": "conversation", "position": P(900, 0),
     "instructions": auth["identify"]["instructions"]},
    {"id": "send_otp", "type": "tool", "position": P(1200, 200), "tool": "api_send_otp_request",
     "args": {"Channel": "=WhatsApp"}},
    {"id": "ask_otp", "type": "conversation", "position": P(1500, 200),
     "tools": ["api_verify_otp", "api_send_otp_request"],
     "instructions": auth["verify_otp"]["instructions"]
     + "\nIf sending the code failed, apologise briefly and offer to send it by text message."},
    {"id": "verify_otp", "type": "tool", "position": P(1800, 200), "tool": "api_verify_otp",
     "args": {"Otp": "parsed.code"}},
    {"id": "verification_failed", "type": "transfer", "position": P(1800, 420),
     "reason": "verification code attempts exhausted"},
    {"id": "confirm_identity", "type": "conversation", "position": P(2100, 200),
     "instructions": ("The caller is verified. The system asks \"Am I speaking to <name>?\" and handles the answer "
                      "(a clear no transfers the call). Once they confirm, the booking starts.")},
    {"id": "hospital", "type": "conversation", "position": P(2400, 200),
     "tools": HOSPITAL_TOOLS + book["hospital"]["tools"], "auto_call": book["hospital"]["auto_call"],
     "instructions": book["hospital"]["instructions"]},
    {"id": "symptoms_and_clinic", "type": "conversation", "position": P(2700, 200),
     "tools": HOSPITAL_TOOLS + book["symptoms_and_clinic"]["tools"],
     "auto_call": book["symptoms_and_clinic"]["auto_call"], "instructions": book["symptoms_and_clinic"]["instructions"]},
    {"id": "doctor_and_time", "type": "conversation", "position": P(3000, 200),
     "tools": HOSPITAL_TOOLS + book["doctor_and_time"]["tools"], "instructions": book["doctor_and_time"]["instructions"]},
    {"id": "confirm_booking", "type": "conversation", "position": P(3300, 200),
     # no booking tool here: the platform books on the caller's yes (the model re-calling it skipped the read-back)
     "tools": HOSPITAL_TOOLS + [t for t in book["doctor_and_time"]["tools"] if t != "api_book_Appointment"],
     "instructions": ("The booking is parked until the caller says yes — NOTHING is booked yet. " + readback_text()
                      + "\nIf the caller wants a different time, doctor, day, clinic or hospital, handle that change "
                        "instead (the parked booking is dropped).")},
    {"id": "booked", "type": "conversation", "position": P(3600, 200), "extract": ["next_step"],
     "tools": HOSPITAL_TOOLS + book["after_booking"]["tools"], "instructions": book["after_booking"]["instructions"]},
    {"id": "anything_else", "type": "conversation", "position": P(3900, 200), "extract": ["next_step"],
     "instructions": ("If the appointment was just confirmed, say exactly: English \"Your appointment is confirmed "
                      "successfully. Is there anything else I can help you with?\" / Arabic \"تم تأكيد موعدك بنجاح. "
                      "تحتاج أي خدمة ثانية؟\". Otherwise ask if they need anything else (\"Is there anything else I "
                      "can help you with?\" / \"تحتاج أي خدمة ثانية؟\") and wait for the answer.")},
    {"id": "new_booking", "type": "set", "position": P(3900, 420),
     "set": {"intent": "=book", "next_step": None, "project_id": None, "project_name": None, "project_name_en": None,
             "clinic_id": None, "date_mode": None, "date": None, "booked": None, "appointment": None,
             "appointment_no": None, "doctor_id": None, "slot_date": None, "slot_time": None, "confirm_done": None,
             "location_lat": None, "location_lng": None}},
    {"id": "goodbye", "type": "end", "position": P(4200, 200),
     "say": {"ar": "شكراً لاتصالك بمجموعة الدكتور سليمان الحبيب، مع السلامة.",
             "en": "Thank you for calling Dr. Sulaiman Al Habib Medical Group. Goodbye!"}},
]

W = lambda **kw: kw  # noqa: E731
edges = [
    # request
    W(**{"from": "greeting_intent", "to": "hospital", "when": {"all": [{"equals": {"intent": "book"}},
                                                                        {"equals": {"identity_confirmed": True}}]}}),
    W(**{"from": "greeting_intent", "to": "ask_mobile", "when": {"equals": {"intent": "book"}}}),
    W(**{"from": "greeting_intent", "to": "other_service", "when": {"equals": {"intent": "other"}}}),
    # verification
    W(**{"from": "ask_mobile", "to": "lookup_patient", "when": {"equals": {"mobile_heard": True}}}),
    W(**{"from": "ask_mobile", "to": "no_file", "when": {"equals": {"files_found": 0}}}),         # the model looked it up
    W(**{"from": "ask_mobile", "to": "choose_file", "when": {"stage": "awaiting_dob_and_name"}}),
    W(**{"from": "ask_mobile", "to": "send_otp", "when": {"stage": "send_otp"}}),
    W(**{"from": "lookup_patient", "to": "no_file", "result": "success", "when": {"equals": {"files_found": 0}}}),
    W(**{"from": "lookup_patient", "to": "choose_file", "result": "success", "when": {"stage": "awaiting_dob_and_name"}}),
    W(**{"from": "lookup_patient", "to": "send_otp", "result": "success", "when": {"stage": "send_otp"}}),
    W(**{"from": "lookup_patient", "to": "ask_mobile", "result": "failure"}),
    W(**{"from": "no_file", "to": "lookup_patient", "when": {"equals": {"mobile_heard": True}}}),
    W(**{"from": "no_file", "to": "goodbye", "when": {"equals": {"next_step": "done"}}}),
    W(**{"from": "choose_file", "to": "send_otp", "when": {"stage": "send_otp"}}),
    W(**{"from": "send_otp", "to": "ask_otp", "result": "success"}),
    W(**{"from": "send_otp", "to": "ask_otp", "result": "failure"}),
    W(**{"from": "ask_otp", "to": "verify_otp", "when": {"equals": {"code_heard": True}}}),
    W(**{"from": "ask_otp", "to": "confirm_identity", "when": {"equals": {"verified": True}}}),   # the model verified it
    W(**{"from": "verify_otp", "to": "confirm_identity", "result": "success"}),
    W(**{"from": "verify_otp", "to": "verification_failed", "result": "failure", "when": {"equals": {"otp_exhausted": True}}}),
    W(**{"from": "verify_otp", "to": "ask_otp", "result": "failure"}),
    W(**{"from": "confirm_identity", "to": "hospital", "when": {"equals": {"identity_confirmed": True}}}),
    # booking
    W(**{"from": "hospital", "to": "symptoms_and_clinic", "when": {"filled": ["project_id"]}}),
    W(**{"from": "symptoms_and_clinic", "to": "doctor_and_time", "when": {"filled": ["clinic_id"]}}),
    W(**{"from": "symptoms_and_clinic", "to": "hospital", "when": {"empty": ["project_id"]}}),
    W(**{"from": "doctor_and_time", "to": "booked", "when": {"filled": ["booked"]}}),
    W(**{"from": "doctor_and_time", "to": "confirm_booking", "when": {"equals": {"awaiting_confirmation": True}}}),
    W(**{"from": "doctor_and_time", "to": "symptoms_and_clinic", "when": {"empty": ["clinic_id"]}}),
    W(**{"from": "confirm_booking", "to": "booked", "when": {"filled": ["booked"]}}),
    W(**{"from": "confirm_booking", "to": "symptoms_and_clinic", "when": {"empty": ["clinic_id"]}}),
    W(**{"from": "confirm_booking", "to": "doctor_and_time", "when": {"equals": {"awaiting_confirmation": False}}}),
    W(**{"from": "booked", "to": "anything_else", "when": {"filled": ["confirm_done"]}}),
    W(**{"from": "booked", "to": "goodbye", "when": {"equals": {"next_step": "done"}}}),
    W(**{"from": "booked", "to": "new_booking", "when": {"equals": {"next_step": "new_booking"}}}),
    W(**{"from": "booked", "to": "other_service", "when": {"equals": {"next_step": "other_request"}}}),
    # wrap-up
    W(**{"from": "anything_else", "to": "goodbye", "when": {"equals": {"next_step": "done"}}}),
    W(**{"from": "anything_else", "to": "new_booking", "when": {"equals": {"next_step": "new_booking"}}}),
    W(**{"from": "anything_else", "to": "other_service", "when": {"equals": {"next_step": "other_request"}}}),
    W(**{"from": "new_booking", "to": "hospital"}),
]

graph = {
    "start": "greeting_intent",
    "variables": {
        "intent": {"type": "string", "enum": ["book", "other"],
                   "description": "book = a new appointment; other = any other request they made (cancel, reschedule, "
                                  "results, reports, insurance, a complaint …); null if they haven't said what they need"},
        "next_step": {"type": "string", "enum": ["done", "new_booking", "other_request"],
                      "description": "only when answering 'anything else?' or after a booking: done = nothing else / "
                                     "goodbye / thanks; new_booking = wants another appointment; other_request = "
                                     "anything else they ask for; null if they are still busy with this booking "
                                     "(e.g. answering whether to confirm it)"},
    },
    "infer": book_cfg["infer"],
    "reset": book_cfg["reset"],
    "nodes": nodes,
    "edges": edges,
}

if __name__ == "__main__":
    out = Path(__file__).parent / "flow.yaml"
    header = ("# The whole HMG call as one flow graph. Generated by build.py from the tested step texts of\n"
              "# skills/authenticate + skills/book_appointment; edit here or on the console canvas.\n")
    out.write_text(header + yaml.safe_dump(graph, allow_unicode=True, sort_keys=False, width=110), encoding="utf-8")
    print(f"{out}: {len(nodes)} nodes, {len(edges)} edges")
