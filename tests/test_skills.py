"""Skill files, step machine, routing, and the conversation rules added with Phase 5."""

import asyncio

import pytest

from runtime.data.reference import set_reference
from runtime.events import EventBus, EventType
from runtime.harness.engine import Agent, SentenceChunker
from runtime.harness.session import Session
from runtime.skills import FileSkillSet, Flow, parse_skill_md
from runtime.tools import MockMCP
from runtime.tools.factory import build_tooling

from .test_harness import Output, ScriptedLLM
from .test_local_tools import REF

# ---------------------------------------------------------------- format


def test_parse_skill_md_language_sections():
    s = parse_skill_md("---\nname: x\ndescription: d\nkeywords: [a, b]\n---\ncommon\n## ar\nعربي\n## en\nenglish\n", "x")
    assert s.description == "d" and s.keywords == ["a", "b"]
    assert s.text("ar") == "common\n\nعربي" and s.text("en") == "common\n\nenglish"


def test_flow_steps_inference_and_reset(tmp_path):
    f = tmp_path / "flow.yaml"
    f.write_text("""
reset: {a: [b]}
infer:
  t1: {on: {result.status: ok}, set: {a: result.id}}
  t2: {set: {b: args.x, mode: "=fixed"}}
steps:
  - {id: one, until: [a], tools: [t1]}
  - {id: two, requires: [a], until: [b], tools: [t2]}
  - {id: three, requires: [b]}
""", encoding="utf-8")
    flow, slots = Flow.load(f), {}
    assert flow.current(slots).id == "one"
    assert flow.apply("t1", {}, {"status": "no"}, True, slots) == {}          # condition not met
    assert flow.apply("t1", {}, {"status": "ok", "id": 7}, True, slots) == {"a": 7}
    assert flow.current(slots).id == "two"
    flow.apply("t2", {"x": 3}, None, True, slots)
    assert slots == {"a": 7, "b": 3, "mode": "fixed"} and flow.current(slots).id == "three"
    flow.apply("t1", {}, {"status": "ok", "id": 8}, True, slots)             # new `a` clears dependent `b`
    assert slots == {"a": 8, "mode": "fixed"} and flow.current(slots).id == "two"
    assert flow.apply("t2", {"x": 1}, None, False, slots) == {}              # failed tool calls infer nothing


# ---------------------------------------------------------------- real skill files


@pytest.fixture
async def skills():
    executor = await build_tooling(MockMCP())
    return FileSkillSet(executor.catalog)


async def test_skill_files_load_and_validate(skills):
    assert {"_persona", "home", "authenticate", "book_appointment"} <= set(skills.skills)
    assert "authenticate" not in skills.routable() and "book_appointment" in skills.routable()
    assert skills.persona("ar").startswith("أنت موظف خدمة عملاء")


async def test_auth_steps_expose_only_stage_tools(skills):
    s = Session(call_id="x")
    assert skills.tools("authenticate", s) == {"mssql_get_patient_info"}
    s.auth.patient_id = 1
    assert skills.tools("authenticate", s) == {"api_send_otp_request"}
    s.auth.otp_sent = True
    assert skills.tools("authenticate", s) == {"api_verify_otp", "api_send_otp_request"}


async def test_booking_progress_follows_tool_calls(skills):
    s = Session(call_id="x")
    step = lambda: skills.current_step("book_appointment", s)  # noqa: E731
    assert step() == "hospital" and "api_book_Appointment" not in skills.tools("book_appointment", s)
    skills.on_tool("book_appointment", "find_hospital_by_name", {}, {"status": "match", "hospital":
                   {"project_id": 12, "name_ar": "مستشفى العليا"}}, True, s)
    assert step() == "symptoms_and_clinic"
    skills.on_tool("book_appointment", "mssql_get_TopFive_availableDoctors_with_slots_byDate",
                   {"projectId": 12, "clinicId": 5, "date": "2026-09-29"}, {}, True, s)
    assert step() == "doctor_and_time" and s.slots["date_mode"] == "date"
    assert "api_book_Appointment" in skills.tools("book_appointment", s)
    # the non-paginated duplicates are never offered
    assert not {"mssql_get_availableDoctors_with_slots_byDate", "mssql_get_nearestClinic_have_doctorSlots"} & \
        skills.tools("book_appointment", s)
    skills.on_tool("book_appointment", "api_book_Appointment", {"DoctorID": 1}, {"AppointmentNo": 99}, True, s)
    assert step() == "after_booking" and s.slots["appointment_no"] == 99


@pytest.mark.parametrize("text,skill", [
    ("ابي احجز موعد عند دكتور جلدية", "book_appointment"), ("ابي الغي موعدي", "manage_appointment"),
    ("ابي نتيجة التحليل", "medical_reports"), ("عندي شكوى", "complaints"),
    ("ارسل لي موقع المستشفى واتساب", "send_info"), ("وش حالة موافقة التامين", "insurance"),
    ("I want to book an appointment", "book_appointment"), ("مرحبا", None), (None, None),
])
async def test_keyword_routing(skills, text, skill):
    assert skills.classify(text) == skill


# ---------------------------------------------------------------- engine + skills


@pytest.fixture
async def rig():
    set_reference(REF)
    bus = EventBus()
    events = []

    async def collect(e):
        events.append(e)

    bus.subscribe(collect)
    await bus.start()
    mock = MockMCP()
    executor = await build_tooling(mock)
    llm, out = ScriptedLLM(), Output()
    agent = Agent(Session(call_id="c"), executor, llm, FileSkillSet(executor.catalog), out, bus.bind(),
                  filler_after_s=5)
    yield agent, llm, mock, out, events, bus
    await bus.stop()


async def test_verified_caller_is_routed_by_first_request(rig):
    agent, llm, mock, out, events, bus = rig
    mock.on("api_verify_otp", {"success": True})
    a = agent.s.auth
    a.patient_id, a.otp_sent = 1, True
    agent.s.pending_intent = "ابي احجز موعد"
    llm.then(("api_verify_otp", {"Otp": "1234"}))
    await agent.handle("1234")
    assert agent.s.active_skill == "book_appointment"          # no switch_skill round trip needed
    llm.then("ودك تحجز بمستشفى معين؟")
    await agent.handle("نعم")                                    # "Am I speaking to …?" → yes
    assert "find_hospital_by_name" in llm.requests[-1]["tools"]
    await bus.stop()
    assert any(e.type == EventType.STEP_TRANSITION and e.data["step"] == "book_appointment/hospital" for e in events)


async def test_one_question_per_reply(rig):
    agent, llm, mock, out, *_ = rig
    agent.s.auth.verified, agent.s.auth.patient_id, agent.s.active_skill = True, 1, "book_appointment"
    llm.then("أنصحك بعيادة الجلدية، تناسبك؟ تبي أقرب موعد ولا تاريخ معين؟")
    await agent.handle("عندي حبوب")
    assert out.said == ["أنصحك بعيادة الجلدية، تناسبك؟"]
    assert agent.s.history[-1]["content"] == "أنصحك بعيادة الجلدية، تناسبك؟"


async def test_no_action_on_an_answer_not_yet_heard(rig):
    agent, llm, mock, out, *_ = rig
    agent.s.auth.verified, agent.s.auth.patient_id, agent.s.active_skill = True, 1, "book_appointment"
    agent.s.slots.update({"project_id": 12, "clinic_id": 5})
    llm.then("عنده الساعة ثمان أو ثمان وربع، أي وقت يناسبك؟",
             ("api_book_Appointment", {"ProjectID": 12, "ClinicID": 5, "DoctorID": 1, "StartTime": "08:00",
                                       "StrAppointmentDate": "2026-09-29"}),
             ("end_call", {}))
    await agent.handle("ايه هذا الدكتور")
    assert mock.calls == [] and agent.s.pending_action is None and not out.hung_up
    assert len(llm.requests) == 1                                # turn ended: waiting for the caller


async def test_one_yes_authorizes_one_action(rig):
    agent, llm, mock, out, *_ = rig
    agent.s.auth.verified, agent.s.auth.patient_id, agent.s.active_skill = True, 1, "book_appointment"
    agent.s.slots.update({"project_id": 12, "clinic_id": 5})
    mock.on("api_book_Appointment", {"AppointmentNo": 55})
    mock.on("mssql_confirm_appointment", {"success": True})
    agent.s.memory["offered_slots"] = {1: {"2026-09-29": {"08:00"}}}
    args = {"ProjectID": 12, "ClinicID": 5, "DoctorID": 1, "StartTime": "08:00", "StrAppointmentDate": "2026-09-29"}
    llm.then(("api_book_Appointment", args)).then("بحجز لك الساعة ثمان، أحجزه لك؟")
    await agent.handle("الساعة ثمان")
    llm.then(("mssql_confirm_appointment", {"appointment_no": 55, "project_id": 12})).then("تبي أأكد الموعد؟")
    await agent.handle("ايه")
    assert [c[0] for c in mock.calls] == ["api_book_Appointment"]   # confirm needs its own yes


async def test_home_routes_later_requests(rig):
    agent, llm, *_ = rig
    agent.s.auth.verified, agent.s.auth.patient_id, agent.s.active_skill = True, 1, "home"
    llm.then("Would you like a specific hospital?")
    await agent.handle("I want to book an appointment")
    assert agent.s.active_skill == "book_appointment"
    assert "find_hospital_by_name" in llm.requests[-1]["tools"]


async def test_gender_from_patient_record(rig):
    agent, llm, mock, *_ = rig
    mock.on("mssql_get_patient_info", {"success": True, "count": 1, "patients": [
        {"patient_id": 77, "full_name_en": "Raza Asmat", "gender": "M", "dob": "1990-01-01"}]})
    llm.then(("mssql_get_patient_info", {"mobileNo": "0541234567"})).then("sent")
    await agent.handle("0541234567")
    assert agent.s.auth.patient_id == 77 and agent.s.auth.first_name == "Raza"
    assert agent.s.gender.known == "male" and agent.s.gender.source == "patient_record"


def test_chunker_glued_questions_and_abbreviations():
    c = SentenceChunker(first_min=10, min_len=40)
    out = []
    for piece in ["Which one would you prefer?Olaya ", "is close. We suggest Dr. Rania. ", "It is 10.30 now. "]:
        out += c.feed(piece)
    out.append(c.flush())
    assert out == ["Which one would you prefer?", "Olaya is close.", "We suggest Dr. Rania.", "It is 10.30 now.", ""]


async def test_clinics_fetched_by_harness_when_hospital_chosen(rig):
    agent, llm, mock, out, *_ = rig
    agent.s.auth.verified, agent.s.auth.patient_id, agent.s.active_skill = True, 1, "book_appointment"
    mock.on("mssql_get_clinics_for_project", [{"ClinicID": 21, "ClinicDescriptionN": "القلب"}])
    llm.then(("find_hospital_by_name", {"query": "الحمراء"})).then("وش الأعراض اللي تحس فيها؟")
    await agent.handle("مستشفى الحمراء")
    await asyncio.sleep(0.05)                       # background fetch (the caller is answering meanwhile)
    clinic_calls = [c for c in mock.calls if c[0] == "mssql_get_clinics_for_project"]
    assert clinic_calls == [("mssql_get_clinics_for_project", {"projectId": 377, "LanguageID": 1})]
    # the agent asked about symptoms without waiting for the list …
    assert "21:القلب" not in str(llm.requests[-1]["messages"])
    llm.then("سلامتك، ما تشوف شر. أنصحك بعيادة القلب، تناسبك؟")
    await agent.handle("عندي ألم في صدري")
    # … and the list is in context when the caller describes symptoms, without the model asking for it
    assert "21:القلب" in str(llm.requests[-1]["messages"])
    assert len([c for c in mock.calls if c[0] == "mssql_get_clinics_for_project"]) == 1   # not fetched twice


async def test_fast_auth_path_no_llm_tool_hops(rig):
    """Parsed mobile → harness looks up + sends OTP; parsed code → harness verifies. The model only speaks."""
    agent, llm, mock, out, *_ = rig
    mock.on("mssql_get_patient_info", {"success": True, "count": 1, "patients": [
        {"patient_id": 5, "full_name_en": "Ali Test", "gender": "M", "dob": "1990-01-01"}]})
    mock.on("api_send_otp_request", {"success": True, "message": "sent"})
    mock.on("api_verify_otp", {"success": True, "message": "verified"})
    agent.s.pending_intent = "ابي احجز موعد"
    llm.then("علي").then("أرسلت لك رمز على الواتساب، وش الرمز؟")      # name in Arabic letters, prepared in the background
    await agent.handle("0551234567")
    assert [c[0] for c in mock.calls] == ["mssql_get_patient_info", "api_send_otp_request"]
    assert len(llm.requests) == 2 and agent.s.auth.otp_sent
    assert "Arabic letters" in llm.requests[0]["messages"][0]["content"] and llm.requests[0]["tools"] == []
    llm.then(("api_send_otp_request", {})).then("تمام")          # a second WhatsApp code seconds later is refused
    await agent.handle("ما وصلني شي؟")
    assert [c[0] for c in mock.calls].count("api_send_otp_request") == 1
    assert "SMS" in next(m["content"] for m in reversed(agent.s.history) if m.get("role") == "tool")
    llm.then(("api_send_otp_request", {"Channel": "SMS"})).then("أرسلته برسالة نصية، وش الرمز؟")
    await agent.handle("ارسله رسالة")
    assert mock.calls[-1][0] == "api_send_otp_request" and mock.calls[-1][1]["Channel"] == "SMS"
    n = len(llm.requests)
    await agent.handle("واحد اثنين ثلاثة أربعة")
    assert mock.calls[-1] == ("api_verify_otp", {"Otp": "1234", "PatientId": 5, "LanguageID": 1})
    assert agent.s.auth.verified and agent.s.active_skill == "book_appointment"   # routed by the first request
    assert out.said[-1] == "هل أتحدث مع علي؟"                         # the harness asks, name in Arabic letters
    assert len(llm.requests) == n                                     # name was ready: no model call this turn
    llm.then("ممكن تعطيني موقعك عشان أحدد لك أقرب المستشفيات، ولا تبي تحجز في مستشفى معين؟")
    await agent.handle("نعم")
    assert agent.s.auth.identity_confirmed and "find_hospital_by_name" in llm.requests[-1]["tools"]


async def test_his_otp_message_is_read_verbatim_then_identity_is_confirmed(rig):
    agent, llm, mock, out, *_ = rig
    msg = "We have sent an OTP to your registered mobile number via whatsapp that ends with 2968"
    agent.s.language.update("I want to book an appointment")
    mock.on("mssql_get_patient_info", {"success": True, "count": 1, "patients": [
        {"patient_id": 5, "full_name_en": "Raza Asmatullah", "gender": "M", "dob": "1990-01-01"}]})
    mock.on("api_send_otp_request", {"success": True, "message": msg})
    mock.on("api_verify_otp", {"success": True, "message": "verified"})
    agent.s.pending_intent = "I want to book an appointment"
    await agent.handle("0551234567")
    assert out.said[-2:] == [msg, "Could you please tell me the code?"]
    assert len(llm.requests) == 0                                   # the model didn't paraphrase it
    await agent.handle("1 2 3 4")
    assert agent.s.auth.verified and not agent.s.auth.identity_confirmed
    assert out.said[-1] == "Am I speaking to Raza Asmatullah?" and len(llm.requests) == 0
    llm.then("Sorry, am I speaking to Raza Asmatullah?")
    await agent.handle("sorry what")                                  # unclear → the model reads it and asks again
    assert out.said[-1] == "Sorry, am I speaking to Raza Asmatullah?" and "record_answer" in llm.requests[-1]["tools"]
    llm.then("Please tell me your location so I can find the nearest hospitals to you, or would you like to book "
             "at a specific hospital?")
    await agent.handle("yes")
    assert agent.s.auth.identity_confirmed and agent.s.last_reply == "consumed"



async def test_nearby_hospitals_fetched_by_the_harness_after_the_location(rig):
    agent, llm, mock, out, *_ = rig
    agent.s.auth.verified, agent.s.auth.patient_id, agent.s.auth.identity_confirmed = True, 1, True
    agent.s.active_skill = "book_appointment"
    mock.on("mssql_get_Projects_from_Location", {"projects": [{"ProjectID": 12, "ProjectName": "Olaya", "distance_km": 1.0}]})
    llm.then(("resolve_location", {"query": "العليا"})) \
       .then("الأقرب لك: مستشفى العليا. أي واحد يناسبك؟")
    await agent.handle("أنا في العليا")
    names = [n for n, _ in mock.calls]
    assert "mssql_get_Projects_from_Location" in names                  # run by the harness, not a model hop
    assert len(llm.requests) == 2                                       # resolve → (harness: nearby) → speak
