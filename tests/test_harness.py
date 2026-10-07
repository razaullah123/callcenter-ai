"""End-to-end harness tests with a scripted LLM and the mock MCP server (no network)."""

import asyncio
import json

import pytest

from runtime.data.reference import set_reference
from runtime.events import Event, EventBus, EventType
from runtime.harness.engine import Agent, SentenceChunker
from runtime.harness.redact import redact_event
from runtime.harness.session import Session
from runtime.harness.skills import SimpleSkillSet
from runtime.providers import LLMDone, TextDelta, ToolCall, ToolCallsReady
from runtime.tools import MockMCP
from runtime.tools.factory import build_tooling

from .test_local_tools import REF

# ---------------------------------------------------------------- fakes


class ScriptedLLM:
    """Each stream() call consumes the next script: a list of str (spoken text) and (tool, args) tuples."""

    def __init__(self):
        self.scripts: list[list] = []
        self.requests: list[dict] = []

    def then(self, *items):
        self.scripts.append(list(items))
        return self

    async def stream(self, messages, *, tools=None, **_):
        self.requests.append({"messages": messages, "tools": [t["function"]["name"] for t in tools or []]})
        script = self.scripts.pop(0) if self.scripts else ["..."]
        calls = []
        for i, item in enumerate(script):
            if isinstance(item, str):
                for word in item.split(" "):
                    yield TextDelta(word + " ")
                    await asyncio.sleep(0)
            else:
                name, args = item
                calls.append(ToolCall(id=f"c{len(self.requests)}_{i}", name=name, arguments=args,
                                      raw_arguments=json.dumps(args)))
        if calls:
            yield ToolCallsReady(calls)
        yield LLMDone("tool_calls" if calls else "stop")


class Output:
    def __init__(self):
        self.said: list[str] = []
        self.transferred: str | None = None
        self.hung_up = False

    async def say(self, text, *, language, interruptible=True):
        self.said.append(text)

    async def transfer(self, reason):
        self.transferred = reason

    async def hangup(self):
        self.hung_up = True


@pytest.fixture
async def rig():
    set_reference(REF)
    bus = EventBus()
    events: list[Event] = []

    async def collect(e):
        events.append(e)

    bus.subscribe(collect)
    await bus.start()
    mock = MockMCP()
    executor = await build_tooling(mock)
    llm, out = ScriptedLLM(), Output()
    session = Session(call_id="call-1")
    agent = Agent(session, executor, llm, SimpleSkillSet(executor.catalog, hooks={"book_appointment": ["hmg.booking"]}), out, bus.bind(),
                  filler_after_s=0.05)
    yield agent, llm, mock, out, events
    await bus.stop()


def verify(agent):
    a = agent.s.auth
    a.verified, a.patient_id, a.first_name, a.identity_confirmed = True, 4455, "محمد", True
    agent.s.active_skill = "book_appointment"

# ---------------------------------------------------------------- auth


async def test_full_auth_flow_single_record(rig):
    agent, llm, mock, out, events = rig
    mock.on("mssql_get_patient_info", [{"PatientID": 4455, "FirstName": "Mohammed", "FirstNameN": "محمد",
                                        "DateofBirth": "1990-03-15"}])
    mock.on("api_send_otp_request", {"success": True})
    mock.on("api_verify_otp", {"success": True, "message": "verified"})

    await agent.start()
    assert out.said[0].startswith("مرحباً بكم في مجموعة الدكتور سليمان الحبيب")

    llm.then("أبشر، عطني رقم جوالك لو سمحت؟")
    await agent.handle("ابي احجز موعد عند دكتور جلدية")
    assert agent.s.pending_intent == "ابي احجز موعد عند دكتور جلدية"

    llm.then(("mssql_get_patient_info", {"mobileNo": "صفر خمسة اربعة"})) \
       .then(("api_send_otp_request", {})) \
       .then("أرسلت لك رمز التحقق على الواتساب، وش الرمز؟")
    await agent.handle("صفر خمسة أربعة واحد اثنين ثلاثة أربعة خمسة ستة سبعة")
    assert "[parsed: mobile: 0541234567]" in agent.s.history[-7]["content"] or \
        any("mobile: 0541234567" in (m.get("content") or "") for m in agent.s.history)
    name, args = mock.calls[0]
    assert name == "mssql_get_patient_info" and args["mobileNo"] == "0541234567" and args["LanguageID"] == 1
    assert mock.calls[1] == ("api_send_otp_request", {"PatientId": 4455, "LanguageID": 1, "Channel": "WhatsApp"})
    assert agent.s.auth.patient_id == 4455 and agent.s.auth.otp_sent
    # PHI never reaches the LLM
    tool_msgs = [m["content"] for m in agent.s.history if m.get("role") == "tool"]
    assert not any("1990" in c or "Mohammed" in c for c in tool_msgs)

    llm.then(("api_verify_otp", {"Otp": "1234"}))
    await agent.handle("واحد اثنين ثلاثة أربعة")
    assert agent.s.auth.verified and out.said[-1] == "هل أتحدث مع محمد؟"     # asked by the harness, word for word
    assert mock.calls[2] == ("api_verify_otp", {"Otp": "1234", "PatientId": 4455, "LanguageID": 1})
    llm.then(("switch_skill", {"skill": "book_appointment"})) \
       .then("ممكن تعطيني موقعك عشان أحدد لك أقرب المستشفيات، ولا تبي تحجز في مستشفى معين؟")
    await agent.handle("نعم")
    assert agent.s.auth.identity_confirmed and agent.s.active_skill == "book_appointment"
    # booking tools become visible only after verification
    assert "mssql_get_clinics_for_project" not in llm.requests[1]["tools"]
    assert "mssql_get_clinics_for_project" in llm.requests[-1]["tools"]


async def test_auth_gate_blocks_everything_before_verification(rig):
    agent, llm, mock, out, _ = rig
    llm.then(("mssql_get_upcoming_appointment", {})).then("لازم أتحقق من هويتك أول.")
    await agent.handle("وش مواعيدي؟")
    tool_msg = next(m for m in agent.s.history if m.get("role") == "tool")
    assert "not available" in tool_msg["content"] and mock.calls == []


async def test_multiple_records_select_patient(rig):
    agent, llm, mock, out, _ = rig
    mock.on("mssql_get_patient_info", [
        {"PatientID": 1, "FirstNameN": "محمد", "DateofBirth": "1990-03-15T00:00:00"},
        {"PatientID": 2, "FirstNameN": "نورة", "DateofBirth": "1995-07-01T00:00:00"}])
    llm.then(("mssql_get_patient_info", {"mobileNo": "0541234567"})).then("وش تاريخ ميلادك واسمك الأول؟")
    await agent.handle("0541234567")
    assert agent.s.auth.stage == "awaiting_dob_and_name"
    llm.then(("select_patient", {"first_name": "نوره", "date_of_birth": "1 يوليو 1995"})).then("تمام")
    await agent.handle("اسمي نوره ومواليد واحد يوليو الف وتسعمية وخمسة وتسعين")
    assert agent.s.auth.patient_id == 2
    assert agent.s.gender.gender == "female"   # weak evidence from the first name


async def test_wrong_otp_three_times_hands_off(rig):
    agent, llm, mock, out, _ = rig
    a = agent.s.auth
    a.patient_id, a.otp_sent = 4455, True
    mock.on("api_verify_otp", {"success": False, "message": "invalid otp"})
    for _ in range(3):
        llm.then(("api_verify_otp", {"Otp": "9999"})).then("الرمز غلط")
        await agent.handle("تسعة تسعة تسعة تسعة")
    assert out.transferred == "OTP attempts exhausted" and not a.verified


async def test_no_record(rig):
    agent, llm, mock, out, _ = rig
    mock.on("mssql_get_patient_info", [])
    llm.then(("mssql_get_patient_info", {"mobileNo": "0541234567"})).then("ما لقيت ملف")
    await agent.handle("0541234567")
    tool_msg = next(m for m in agent.s.history if m.get("role") == "tool")
    assert '"records_found": 0' in tool_msg["content"] and "support" in tool_msg["content"]

# ---------------------------------------------------------------- confirmation


async def test_booking_requires_readback_then_harness_books_on_yes(rig):
    agent, llm, mock, out, _ = rig
    verify(agent)
    mock.on("api_book_Appointment", {"AppointmentNo": 991, "success": True})
    agent.s.memory["offered_slots"] = {5018: {"2026-10-01": {"10:00"}}}   # offered by a slot search
    args = {"ProjectID": 12, "ClinicID": 5, "DoctorID": 5018, "StartTime": "10:00",
            "StrAppointmentDate": "2026-10-01"}
    llm.then(("api_book_Appointment", args)).then("بحجز لك عند الدكتورة روبينا يوم الخميس الساعة عشر، أأكد الحجز؟")
    await agent.handle("الساعة عشر")
    assert mock.calls == [] and agent.s.pending_action is not None

    llm.then("تم حجز موعدك، تبي أأكد الموعد الحين؟")
    await agent.handle("ايه")
    assert mock.calls == [("api_book_Appointment", {**args, "PatientID": 4455, "LanguageID": 1})]
    assert agent.s.pending_action is None and agent.s.slots["booked"]
    # the LLM was not asked to call the tool again: the harness executed it
    assert all(n != "api_book_Appointment" for n, _ in [(c["function"]["name"], 0) for m in agent.s.history
                                                       for c in (m.get("tool_calls") or [])
                                                       if m.get("tool_calls") and "confirmed" not in c["id"]][1:])


async def test_declined_booking_is_not_executed(rig):
    agent, llm, mock, out, _ = rig
    verify(agent)
    llm.then(("api_book_Appointment", {"ProjectID": 12, "ClinicID": 5, "DoctorID": 1, "StartTime": "10:00",
                                       "StrAppointmentDate": "2026-10-01"})).then("أأكد؟")
    await agent.handle("عشر")
    llm.then("ولا يهمك، تبي وقت ثاني؟")
    await agent.handle("لا")
    assert mock.calls == [] and agent.s.pending_action is None


async def test_affirm_gate(rig):
    agent, llm, mock, out, _ = rig
    verify(agent)
    agent.s.active_skill = "book_appointment"
    mock.on("mssql_confirm_appointment", {"success": True})
    agent.skills.tools = lambda skill, s: {"mssql_confirm_appointment"}
    llm.then(("mssql_confirm_appointment", {"appointment_no": 991, "project_id": 12})).then("تبي أأكده؟")
    await agent.handle("تمام شكرا")   # "تمام" is a yes → allowed
    assert mock.calls[-1][0] == "mssql_confirm_appointment"
    mock.calls.clear()
    llm.then(("mssql_confirm_appointment", {"appointment_no": 991, "project_id": 12})).then("تبي أأكده؟")
    await agent.handle("وش الموعد اللي عندي")  # not a yes → blocked
    assert mock.calls == []

# ---------------------------------------------------------------- safety / handoff


async def test_red_flag_empathy_then_continue(rig):
    agent, llm, mock, out, events = rig
    verify(agent)
    llm.then("سلامتك، آسفين نسمع إنك تعاني من ألم في الصدر. بناءً على ألم الصدر، نقترح لك عيادة القلب. تبي تحجز فيها، "
             "ولا تفضل عيادة ثانية؟")
    await agent.handle("عندي ألم في صدري من اسبوع")
    assert len(llm.requests) == 1                       # booking continues through the LLM
    user_msg = llm.requests[0]["messages"][-1]["content"]
    # default (user decision): empathy + clinic, no ER / 997 line
    assert "empathy naming the symptom" in user_msg and "Don't mention emergency departments or 997" in user_msg
    assert "cardiology" in user_msg                     # chest pain → cardiology hint stays


async def test_red_flag_advise_mode_still_available(rig, monkeypatch):
    from runtime.config import get_settings
    agent, llm, mock, out, events = rig
    verify(agent)
    monkeypatch.setattr(get_settings(), "red_flag_mode", "advise_and_continue")
    llm.then("سلامتك.")
    await agent.handle("عندي ألم في صدري من اسبوع")
    assert "997" in llm.requests[0]["messages"][-1]["content"]


async def test_red_flag_stop_mode(rig, monkeypatch):
    from runtime.config import get_settings
    agent, llm, mock, out, events = rig
    monkeypatch.setattr(get_settings(), "red_flag_mode", "stop")
    await agent.handle("عندي ألم في صدري من ساعة")
    assert llm.requests == [] and "٩٩٧" in out.said[-1]


async def test_human_request_transfers(rig):
    agent, llm, mock, out, _ = rig
    await agent.handle("ابي اكلم موظف")
    assert out.transferred == "caller asked for a human agent" and llm.requests == []
    await agent.handle("hello?")   # after handoff the agent stays silent
    assert llm.requests == []

# ---------------------------------------------------------------- latency features


async def test_filler_spoken_for_slow_tool(rig):
    agent, llm, mock, out, _ = rig
    verify(agent)

    async def slow(args):
        await asyncio.sleep(0.2)
        return [{"ClinicID": 5, "ClinicDescriptionN": "الجلدية"}]

    async def call(name, args, timeout_s):
        mock.calls.append((name, args))
        return True, await slow(args)

    mock.call = call
    agent.s.memory["offered_projects"] = {12}
    llm.then(("mssql_get_clinics_for_project", {"projectId": 12})).then("عندنا عيادة الجلدية.")
    await agent.handle("عندي حبوب بوجهي")
    assert out.said[0] == "لحظة أشيك لك."


async def test_prefetch_clinics_after_hospital_match(rig):
    agent, llm, mock, out, events = rig
    verify(agent)
    mock.on("mssql_get_clinics_for_project", [{"ClinicID": 5, "ClinicDescriptionN": "الجلدية"}])
    llm.then(("find_hospital_by_name", {"query": "الحمراء"})).then("لقيت مستشفى الحمراء، وش الأعراض؟")
    await agent.handle("مستشفى الحمراء")
    await asyncio.sleep(0.05)
    assert mock.calls == [("mssql_get_clinics_for_project", {"projectId": 377, "LanguageID": 1})]  # prefetched
    llm.then(("mssql_get_clinics_for_project", {"projectId": 377})).then("أنصحك بعيادة الجلدية.")
    await agent.handle("حبوب في وجهي")
    assert len(mock.calls) == 1   # served from cache, no second 2 s call
    await asyncio.sleep(0.05)
    assert mock.calls[-1][0] == "mssql_get_TopFive_nearestClinic_have_doctorSlots"   # clinic mentioned → slots
    assert mock.calls[-1][1] == {"projectId": 377, "clinicId": 5}


async def test_barge_in_keeps_history_consistent(rig):
    agent, llm, mock, out, _ = rig
    verify(agent)
    llm.then("هذي جملة أولى طويلة شوي عشان نختبر المقاطعة. وهذي جملة ثانية ما راح تكمل")
    orig_say = out.say

    async def slow_say(text, **kw):
        await orig_say(text, **kw)
        await asyncio.sleep(0.05)

    out.say = slow_say
    task = asyncio.create_task(agent.handle("ابي موعد"))
    while not out.said:                 # barge in while the first sentence is playing
        await asyncio.sleep(0.001)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    last = agent.s.history[-1]
    assert last["role"] == "assistant" and last["content"].endswith("—")
    agent.on_interrupted("هذي جملة")
    assert agent.s.history[-1]["content"] == "هذي جملة —"


async def test_write_in_flight_survives_barge_in(rig):
    agent, llm, mock, out, _ = rig
    verify(agent)
    agent.s.last_reply = "yes"

    async def call(name, args, timeout_s):
        await asyncio.sleep(0.1)
        mock.calls.append((name, args))
        return True, {"success": True}

    mock.call = call
    agent.skills.tools = lambda skill, s: {"mssql_confirm_appointment"}
    llm.then(("mssql_confirm_appointment", {"appointment_no": 7, "project_id": 12}))
    task = asyncio.create_task(agent.handle("ايه أكده"))
    await asyncio.sleep(0.03)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert mock.calls and mock.calls[0][0] == "mssql_confirm_appointment"   # completed despite barge-in
    assert any(m.get("role") == "tool" and "success" in m["content"] for m in agent.s.history)

# ---------------------------------------------------------------- utilities


async def test_model_cannot_choose_hospital_or_time_itself(rig):
    agent, llm, mock, out, _ = rig
    verify(agent)
    llm.then(("mssql_get_clinics_for_project", {"projectId": 12})).then("أي مستشفى تبي؟")
    await agent.handle("ابي عيادة القلب")
    assert mock.calls == [] and "which hospital" in agent.s.history[-2]["content"]
    agent.s.memory["offered_slots"] = {7: {"2026-09-29": {"08:00"}}}
    llm.then(("api_book_Appointment", {"ProjectID": 12, "ClinicID": 21, "DoctorID": 7, "StartTime": "09:00",
                                       "StrAppointmentDate": "2026-09-29"})).then("أي وقت؟")
    await agent.handle("احجز لي")
    assert mock.calls == [] and agent.s.pending_action is None
    assert "not offered" in agent.s.history[-2]["content"]


def test_sentence_chunker():
    c = SentenceChunker(first_min=10, min_len=40)
    out = []
    for piece in ["أهلاً وسهلاً، ", "حياك الله. ", "عندنا ثلاث مستشفيات قريبة منك، ", "أقربها العليا."]:
        out += c.feed(piece)
    out.append(c.flush())
    assert out == ["أهلاً وسهلاً،", "حياك الله.", "عندنا ثلاث مستشفيات قريبة منك، أقربها العليا."]


def test_redaction():
    e = Event(type=EventType.TOOL_START, data={"args": {"mobileNo": "0541234567", "projectId": 12, "Otp": "1234"},
                                               "text": "رقمي 0541234567 وشكرا"})
    r = redact_event(e).data
    assert r["args"]["mobileNo"] == "•••" and r["args"]["Otp"] == "•••" and r["args"]["projectId"] == 12
    assert "0541234567" not in r["text"] and r["text"].endswith("67 وشكرا")


def test_redaction_keeps_dates():
    e = Event(type=EventType.TOOL_START, data={"args": {"StrAppointmentDate": "2026-11-15", "StartTime": "08:00"},
                                               "text": "موعدك 15/11/2026 ورقمي 0541234567"})
    r = redact_event(e).data
    assert r["args"]["StrAppointmentDate"] == "2026-11-15" and "15/11/2026" in r["text"]
    assert "0541234567" not in r["text"]


async def test_greeting_first_is_not_the_request(rig):
    agent, llm, mock, out, _ = rig
    await agent.handle("hi good afternoon")
    assert agent.s.pending_intent is None
    assert out.said[-1] == "Good afternoon! How can I help you today?" and llm.requests == []   # said by the harness
    llm.then("Sure, may I have your registered mobile number?")
    await agent.handle("I want to book an appointment")
    assert agent.s.pending_intent == "I want to book an appointment"


async def test_patient_data_access_is_audited(rig, monkeypatch):
    from runtime.control import audit
    agent, llm, mock, out, _ = rig
    rows = []
    monkeypatch.setattr(audit.AUDIT, "record", lambda **kw: rows.append(kw))
    verify(agent)
    agent.skills.tools = lambda skill, s: {"mssql_get_upcoming_appointment", "api_book_Appointment"}
    mock.on("mssql_get_upcoming_appointment", [])
    llm.then(("mssql_get_upcoming_appointment", {})).then("ما عندك مواعيد.")
    await agent.handle("وش مواعيدي")
    assert rows[-1]["tool"] == "mssql_get_upcoming_appointment" and rows[-1]["patient_id"] == "4455"
    assert rows[-1]["verified"] and rows[-1]["kind"] == "read" and rows[-1]["ok"]
    # a confirmed booking is audited as a confirmed write
    mock.on("api_book_Appointment", {"AppointmentNo": 1})
    agent.s.memory["offered_slots"] = {1: {"2026-10-01": {"08:00"}}}
    args = {"ProjectID": 12, "ClinicID": 5, "DoctorID": 1, "StartTime": "08:00", "StrAppointmentDate": "2026-10-01"}
    llm.then(("api_book_Appointment", args)).then("أحجزه؟")
    await agent.handle("الساعة ثمان")
    llm.then("تم.")
    await agent.handle("ايه")
    booking = [r for r in rows if r["tool"] == "api_book_Appointment"]
    # the parked call never reached the HIS (not audited); the executed one is a confirmed write
    assert [b["confirmed"] for b in booking] == [True] and booking[0]["kind"] == "write"


def test_production_safety_checks():
    from runtime.config import Settings
    from runtime.control.maintenance import production_problems
    base = dict(source_database_url=None)
    assert production_problems(Settings(tools_mode="hybrid", console_token=None, auth_secret=None, **base)) == []
    probs = production_problems(Settings(tools_mode="live", console_token=None, auth_secret=None, **base))
    assert any("CONSOLE_TOKEN" in p for p in probs) and any("AUTH_SECRET" in p for p in probs)
    assert production_problems(Settings(tools_mode="live", console_token="x", auth_secret="y", **base)) == []


def test_first_name_matches_across_scripts():
    from runtime.harness.policy import _first_token_match
    from runtime.text.arabic import normalize
    for ar, en in [("عبدالله", "Abdullah"), ("محمد", "Mohammed"), ("نورة", "Noura"), ("خالد", "Khalid"), ("ريم", "Reem")]:
        assert _first_token_match(normalize(ar), normalize(en))
    for ar, en in [("محمد", "Sara"), ("خالد", "Faisal"), ("ريم", "Noura")]:
        assert not _first_token_match(normalize(ar), normalize(en))


def test_bracketed_artifacts_not_spoken():
    from runtime.harness.policy import clean_for_speech
    assert "End" not in clean_for_speech("عطنا تاريخ ميلادك.[End of conversation]")


async def test_unclear_readback_answer_cannot_be_claimed_as_booked(rig):
    agent, llm, mock, out, _ = rig
    verify(agent)
    agent.s.memory["offered_slots"] = {5018: {"2026-10-01": {"10:00"}}}
    args = {"ProjectID": 12, "ClinicID": 5, "DoctorID": 5018, "StartTime": "10:00", "StrAppointmentDate": "2026-10-01"}
    llm.then(("api_book_Appointment", args)).then("بحجز لك عند الدكتورة روبينا يوم الخميس الساعة عشر، أحجزه لك؟")
    await agent.handle("الساعة عشر")
    llm.then("تم الحجز، إذا ما في شيء ثاني، مع السلامة.")        # model hallucinates success on an unclear reply
    await agent.handle("أحبّ")
    assert mock.calls == [] and agent.s.pending_action is not None
    last = agent.s.history[-1]["content"]
    assert "تم الحجز" not in last and "نعم ولا لا" in last
    assert "NOTHING has been booked" in next(m["content"] for m in reversed(agent.s.history) if m["role"] == "user")


def test_feminine_rewrite():
    from runtime.harness.policy import to_feminine
    assert to_feminine("وش الأعراض اللي تحس فيها؟") == "وش الأعراض اللي تحسين فيها؟"
    assert to_feminine("تحجز معها ولا وتبي غيرها؟") == "تحجزين معها ولا وتبين غيرها؟"
    assert to_feminine("تحسين") == "تحسين"


def test_labelled_slot_time_normalized_for_booking():
    from runtime.harness.policy import normalize_datetime_args
    assert normalize_datetime_args({"StartTime": "14:30 (2:30 PM)"})["StartTime"] == "14:30"
    assert normalize_datetime_args({"StartTime": "2:30 PM"})["StartTime"] == "14:30"


async def test_malformed_tool_call_is_retried(rig):
    agent, llm, mock, out, _ = rig
    verify(agent)
    original = llm.stream
    state = {"failed": False}

    async def flaky(messages, *, tools=None, **kw):
        if not state["failed"]:
            state["failed"] = True
            raise RuntimeError("Error code: 400 - {'error': {'code': 'tool_use_failed', "
                               "'message': 'Failed to call a function. Please adjust your prompt.'}}")
        async for ev in original(messages, tools=tools, **kw):
            yield ev

    llm.stream = flaky
    llm.then("سلامتك، ودك أقترح لك عيادة القلب؟")
    await agent.handle("عندي ألم في صدري من أسبوع")
    assert "خلل" not in " ".join(out.said) and "عيادة القلب" in " ".join(out.said)
    assert "malformed" in llm.requests[-1]["messages"][-1]["content"]


async def test_requested_day_must_be_searched_not_inferred_from_earliest(rig):
    agent, llm, mock, out, _ = rig
    verify(agent)
    agent.s.memory["clinics"] = {41: "عيادة صداع الراس"}
    agent.s.slots["project_id"] = 12
    llm.then(("mssql_get_TopFive_nearestClinic_have_doctorSlots", {"projectId": 12, "clinicId": 41})) \
       .then("ما فيه مواعيد يوم الأحد")
    await agent.handle("أبي موعد الأحد الجاي بأول دكتور")
    tool_msg = next(m for m in reversed(agent.s.history) if m.get("role") == "tool")
    assert "availableDoctors_with_slots_byDate" in tool_msg["content"] and mock.calls == []
    # "earliest" lifts it
    llm.then(("mssql_get_TopFive_nearestClinic_have_doctorSlots", {"projectId": 12, "clinicId": 41})).then("تمام")
    mock.on("mssql_get_TopFive_nearestClinic_have_doctorSlots", {"success": True, "doctors": []})
    await agent.handle("طيب أقرب موعد")
    assert [n for n, _ in mock.calls] == ["mssql_get_TopFive_nearestClinic_have_doctorSlots"]


def test_symptom_duration_is_not_a_requested_day():
    from runtime.harness.nlu.intents import mentions_since
    assert mentions_since("عندي صداع من اليوم") and not mentions_since("أبي موعد الأحد الجاي")


async def test_transfer_not_announced_twice(rig):
    agent, llm, mock, out, _ = rig
    verify(agent)
    llm.then("أبشر، بحولك على أحد الزملاء.", ("transfer_to_human", {"reason": "caller asked"}))
    await agent.handle("عندي مشكلة معقدة بالتأمين ما انحلت")
    said = " ".join(out.said)
    assert said.count("الزملاء") == 1 and said.endswith("لحظة من فضلك.")
    assert agent.s.handoff and out.transferred


def test_brackets_not_spoken():
    from runtime.harness.policy import clean_for_speech
    assert clean_for_speech("يوم الثلاثاء (غداً) الساعة ثنين") == "يوم الثلاثاء غداً الساعة ثنين"



async def test_not_the_patient_is_transferred(rig):
    agent, llm, mock, out, _ = rig
    verify(agent)
    agent.s.auth.identity_confirmed = False
    agent.s.memory["identity_asked"] = True
    await agent.handle("no")
    assert agent.s.handoff and "not the registered patient" in agent.s.handoff["reason"]


async def test_his_refusal_is_not_a_booking(rig):
    agent, llm, mock, out, _ = rig
    verify(agent)
    agent.s.memory["offered_slots"] = {5018: {"2026-11-15": {"08:00"}}}
    args = {"ProjectID": 12, "ClinicID": 5, "DoctorID": 5018, "StartTime": "08:00", "StrAppointmentDate": "2026-11-15"}
    mock.on("api_book_Appointment", {"success": False, "message": "sorry ,you can't book this appointment, because "
                                     "you have another appointment in the same time", "appointment_data": args})
    llm.then(("api_book_Appointment", args)).then("أحجز لك الموعد؟")
    await agent.handle("الساعة ثمان")
    llm.then("تم حجز موعدك.")                                     # the model misreads the refusal
    await agent.handle("نعم")
    assert not agent.s.slots.get("booked") and "api_book_Appointment" not in agent._done_tools
    assert "تم حجز" not in " ".join(out.said[-2:])


async def test_one_short_note_may_follow_the_question(rig):
    agent, llm, mock, out, _ = rig
    verify(agent)
    agent.s.language.update("hello there")
    llm.then("The available time slots with Dr. Rubeena Quadri are Sunday at ten, ten fifteen and ten thirty in the "
             "morning. Which time slot suits you? There are more slots available, so please let me know if you need "
             "another time. Also, do you want a reminder?")
    await agent.handle("the first doctor please")
    said = " ".join(out.said)
    assert "Which time slot suits you?" in said and "more slots available" in said and "reminder" not in said


def test_reasoning_leaks_are_not_spoken():
    from runtime.harness.policy import clean_for_speech, is_reasoning_leak
    assert is_reasoning_leak("We need to list three nearest hospitals correctly.")
    assert is_reasoning_leak("The user wants a doctor.") and is_reasoning_leak("We should call the tool now.")
    assert not is_reasoning_leak("I need to check that for you.") and not is_reasoning_leak("We need your mobile number.")
    assert clean_for_speech("Olaya Hospital, and Home Home Home") == "Olaya Hospital, and Home"


async def test_serious_symptom_during_identity_question_is_not_swallowed(rig):
    agent, llm, mock, out, _ = rig
    verify(agent)
    agent.s.auth.identity_confirmed, agent.s.memory["identity_asked"] = False, True
    llm.then("سلامتك، إذا صار شديد أو فجأة روح أقرب طوارئ أو اتصل ٩٩٧. هل أتحدث مع محمد؟")
    await agent.handle("عندي ألم في صدري من أسبوع")
    assert "٩٩٧" in " ".join(out.said) and len(llm.requests) == 1
    assert "هل أتحدث مع" in llm.requests[-1]["messages"][0]["content"]       # identity step instructions



async def test_no_goodbye_after_anything_else_question(rig):
    agent, llm, mock, out, _ = rig
    verify(agent)
    llm.then("طيب، تبي شي ثاني؟ حياك الله، مع السلامة.")
    await agent.handle("نعم أكده")
    said = " ".join(out.said)
    assert "تبي شي ثاني؟" in said and "مع السلامة" not in said


async def test_date_question_before_earliest_search(rig):
    agent, llm, mock, out, _ = rig
    verify(agent)
    agent.s.memory["clinics"] = {12: "العظام"}
    agent.s.slots["project_id"] = 12
    llm.then(("mssql_get_TopFive_nearestClinic_have_doctorSlots", {"projectId": 12, "clinicId": 12})) \
       .then("تبي أقرب موعد متاح، ولا عندك تاريخ معين؟")
    await agent.handle("نعم عيادة العظام تناسبني")                    # agreed to the clinic, said nothing about when
    tool_msg = next(m for m in reversed(agent.s.history) if m.get("role") == "tool")
    assert "earliest available appointment" in tool_msg["content"] and mock.calls == []
    mock.on("mssql_get_TopFive_nearestClinic_have_doctorSlots", {"success": True, "doctors": []})
    llm.then(("mssql_get_TopFive_nearestClinic_have_doctorSlots", {"projectId": 12, "clinicId": 12})).then("تمام")
    await agent.handle("أقرب موعد")
    assert [n for n, _ in mock.calls] == ["mssql_get_TopFive_nearestClinic_have_doctorSlots"]


def test_gibberish_and_loose_date_answers():
    from runtime.harness.nlu.intents import loose_earliest
    from runtime.harness.policy import is_garbled
    assert is_garbled("تبي أقرب تاريخ تاريخ … أقرب تو تو ت ت لل الت الت ك ك… حا")
    assert not is_garbled("I am on it, is it ok by me to go to it?")
    assert not is_garbled("بناءً على ألم الركبة، نقترح لك عيادة العظام. تبي تحجز فيها، ولا تفضل عيادة ثانية؟")
    assert not is_garbled("وش حي أو مدينة تقيمين فيها؟")          # real 2-letter words (eval 2026-09-29: dead air)
    assert loose_earliest("أول دكتور") and loose_earliest("ما يفرق") and loose_earliest("any is fine")
    assert not loose_earliest("الأحد الجاي") and not loose_earliest("لا")


async def test_no_booking_in_the_reply_that_offers_times(rig):
    agent, llm, mock, out, _ = rig
    verify(agent)
    agent.s.memory["offered_slots"] = {5018: {"2026-10-01": {"14:30"}}}
    args = {"ProjectID": 12, "ClinicID": 5, "DoctorID": 5018, "StartTime": "14:30", "StrAppointmentDate": "2026-10-01"}
    llm.then("الأوقات المتاحة مع الدكتورة روبينا قادري هي الثلاثاء الساعة اثنين ونص، وثلاث، وثلاث ونص. أي وقت يناسبك؟ "
             "فيه أوقات ثانية متاحة، إذا تبي وقت ثاني علمني.", ("api_book_Appointment", args))
    await agent.handle("نعم الدكتورة روبينا")
    assert agent.s.pending_action is None and mock.calls == []        # waits for the caller to pick a time


async def test_booked_line_said_by_harness_on_yes(rig):
    agent, llm, mock, out, _ = rig
    verify(agent)
    agent.s.language.update("hello there")
    mock.on("api_book_Appointment", {"success": True, "appointment_data": {"AppointmentNo": "222854879"}})
    agent.s.memory["offered_slots"] = {5018: {"2026-11-15": {"08:00"}}}
    args = {"ProjectID": 12, "ClinicID": 5, "DoctorID": 5018, "StartTime": "08:00", "StrAppointmentDate": "2026-11-15"}
    llm.then(("api_book_Appointment", args)).then("Shall I book your appointment with Dr. Rubeena Quadri?")
    await agent.handle("8 am")
    n = len(llm.requests)
    await agent.handle("yes please")
    assert out.said[-1] == "Your appointment is booked successfully. Would you like me to confirm it now?"
    assert len(llm.requests) == n and agent.s.slots.get("booked")        # no model round trip for this line


def test_booking_times_with_am_pm_in_both_languages():
    from runtime.harness.policy import normalize_datetime_args as n
    cases = {"8:15 صباحاً": "08:15", "2:30 مساءً": "14:30", "3 مساءً": "15:00", "8 AM": "08:00", "2:30 p.m.": "14:30",
             "12 PM": "12:00", "14:30 (2:30 PM / 2:30 مساءً)": "14:30"}
    for raw, want in cases.items():
        assert n({"StartTime": raw})["StartTime"] == want, raw


def test_zero_width_characters_removed_from_speech():
    from runtime.harness.policy import clean_for_speech
    assert clean_for_speech("في عيادة العظام ب​مستشفى العليا") == "في عيادة العظام بمستشفى العليا"



async def test_greeting_back_matches_the_callers_greeting(rig):
    agent, llm, mock, out, _ = rig
    await agent.handle("مرحباً، كيف حالك؟")
    assert out.said[-1] == "هلا والله، مرحبتين. الحمدلله بخير، الله يسلمك. كيف أقدر أخدمك اليوم؟"
    assert "السلام" not in out.said[-1] and agent.s.pending_intent is None


async def test_model_reads_the_date_answer_even_when_misheard(rig):
    agent, llm, mock, out, _ = rig
    verify(agent)
    agent.s.memory["clinics"] = {9: "الجهاز الهضمي"}
    agent.s.slots["project_id"] = 12
    llm.then("تبي أقرب موعد متاح، ولا عندك تاريخ معين؟")
    await agent.handle("ومناسبة")                                  # agreed to the clinic → date question
    mock.on("mssql_get_TopFive_nearestClinic_have_doctorSlots", {"success": True, "doctors": []})
    llm.then('{"date": null, "earliest": true, "confidence": "high"}')        # the extraction reads it
    llm.then(("mssql_get_TopFive_nearestClinic_have_doctorSlots", {"projectId": 12, "clinicId": 9})).then("تمام")
    await agent.handle("أبي أغري في موعد مطاح")                   # STT garbled "أبي أقرب موعد متاح"
    assert [n for n, _ in mock.calls] == ["mssql_get_TopFive_nearestClinic_have_doctorSlots"]   # not blocked
    assert "earliest available appointment" in next(m["content"] for m in reversed(agent.s.history)
                                                     if m["role"] == "user")



async def test_model_reads_a_loose_identity_answer(rig):
    agent, llm, mock, out, _ = rig
    verify(agent)
    agent.s.auth.identity_confirmed, agent.s.memory["identity_asked"] = False, True
    llm.then(("record_answer", {"question": "identity", "answer": "yes"})) \
       .then("ممكن تعطيني موقعك عشان أحدد لك أقرب المستشفيات، ولا تبي تحجز في مستشفى معين؟")
    await agent.handle("حسنا، أنا هنا")                              # not a plain yes — the model reads it
    assert agent.s.auth.identity_confirmed and "موقعك" in " ".join(out.said)
    assert "record_answer" not in llm.requests[-1]["tools"]          # nothing is waiting any more


async def test_model_reads_a_loose_booking_confirmation(rig):
    agent, llm, mock, out, _ = rig
    verify(agent)
    agent.s.language.update("hello there")
    mock.on("api_book_Appointment", {"success": True, "appointment_data": {"AppointmentNo": "1"}})
    agent.s.memory["offered_slots"] = {5018: {"2026-10-01": {"10:00"}}}
    args = {"ProjectID": 12, "ClinicID": 5, "DoctorID": 5018, "StartTime": "10:00", "StrAppointmentDate": "2026-10-01"}
    llm.then(("api_book_Appointment", args)).then("Shall I book your appointment with Dr. Rubeena Quadri?")
    await agent.handle("10 AM")
    llm.then(("record_answer", {"question": "booking_confirmation", "answer": "yes"}))
    await agent.handle("go for it")                                   # not in the yes / no word lists
    assert [n for n, _ in mock.calls] == ["api_book_Appointment"] and agent.s.slots.get("booked")
    assert out.said[-1] == "Your appointment is booked successfully. Would you like me to confirm it now?"


async def test_model_cannot_answer_its_own_question(rig):
    agent, llm, mock, out, _ = rig
    verify(agent)
    agent.s.memory["offered_slots"] = {5018: {"2026-10-01": {"10:00"}}}
    args = {"ProjectID": 12, "ClinicID": 5, "DoctorID": 5018, "StartTime": "10:00", "StrAppointmentDate": "2026-10-01"}
    llm.then(("api_book_Appointment", args)).then("أحجز لك الموعد؟")
    await agent.handle("الساعة عشر")
    # the model asks again AND records a yes in the same reply → refused
    llm.then("تبي أحجزه لك؟", ("record_answer", {"question": "booking_confirmation", "answer": "yes"}))
    await agent.handle("همم")
    assert mock.calls == [] and agent.s.pending_action is not None


async def test_model_cannot_confirm_in_the_turn_that_parked_the_booking(rig):
    # live 2026-10-04: "Please book for 8 am" → booking parked → the model recorded "yes" itself in the same turn
    # and the harness booked with no read-back
    agent, llm, mock, out, _ = rig
    verify(agent)
    agent.s.language.update("hello there")
    mock.on("api_book_Appointment", {"success": True, "appointment_data": {"AppointmentNo": "1"}})
    agent.s.memory["offered_slots"] = {5018: {"2026-10-01": {"10:00"}}}
    args = {"ProjectID": 12, "ClinicID": 5, "DoctorID": 5018, "StartTime": "10:00", "StrAppointmentDate": "2026-10-01"}
    llm.then(("api_book_Appointment", args)) \
       .then(("record_answer", {"question": "booking_confirmation", "answer": "yes"})) \
       .then("I'll book you with Dr. Rubeena Quadri on Thursday at 10 AM. Shall I go ahead?")
    await agent.handle("Please book for 10 am")
    assert mock.calls == [] and agent.s.pending_action is not None and not agent.s.slots.get("booked")
    assert out.said[-1].endswith("Shall I go ahead?")
    llm.then("Your appointment is booked.")
    await agent.handle("yes")                                          # the real answer, next turn
    assert [n for n, _ in mock.calls] == ["api_book_Appointment"] and agent.s.slots.get("booked")


async def test_chosen_hospital_named_in_the_call_language(rig):
    from runtime.harness.context import system_prompt
    skills = rig[0].skills
    s = Session(call_id="c")
    s.auth.verified = s.auth.identity_confirmed = True
    s.slots.update({"project_id": 12, "project_name": "مستشفى العليا", "project_name_en": "Olaya Hospital"})
    s.language.update("I want to book an appointment")
    en = system_prompt(s, skills)
    assert '"project_name": "Olaya Hospital"' in en and "مستشفى العليا" not in en   # it said "Al-Ula Hospital"
    s2 = Session(call_id="c2")
    s2.auth.verified = s2.auth.identity_confirmed = True
    s2.slots.update(s.slots)
    assert '"project_name": "مستشفى العليا"' in system_prompt(s2, skills)


def test_location_phrasing_is_not_a_hospital_name():
    from runtime.harness.nlu.intents import describes_location
    assert describes_location("Currently I am located nearby Olayya.") and describes_location("أنا في العليا")
    assert not describes_location("I'm looking for Olaya Hospital") and not describes_location("أبي مستشفى العليا")


async def test_caller_who_carries_on_is_not_stuck_on_the_name_question(rig):
    agent, llm, mock, out, _ = rig
    verify(agent)
    agent.s.auth.identity_confirmed, agent.s.memory["identity_asked"] = False, True
    llm.then("هل أتحدث مع محمد؟")
    await agent.handle("حسنا، أنا هنا")                          # 1st unclear reply: the model may ask again
    assert not agent.s.auth.identity_confirmed
    llm.then("وش الأعراض اللي تعاني منها، عشان أقترح لك العيادة المناسبة؟")
    await agent.handle("مستشفى العليا")                           # carries on with the request → implicit yes
    assert agent.s.auth.identity_confirmed and "record_answer" not in llm.requests[-1]["tools"]
    await agent.handle("لا")                                      # a later "no" is not about the name any more
    assert not agent.s.handoff


# ---------------------------------------------------------------- LLM extraction when the patterns miss

def test_extraction_is_validated():
    from datetime import date
    from runtime.harness.nlu.extract import validate
    today = date(2026, 9, 28)
    assert validate("mobile", {"mobile": "0548802968", "confidence": "high"}, today=today) == "0548802968"
    assert validate("mobile", {"mobile": "054880296", "confidence": "high"}, today=today) is None        # 9 digits
    assert validate("mobile", {"mobile": "0548802968", "confidence": "low"}, today=today) is None        # unsure
    assert validate("otp", {"code": "46 73 15"}, today=today) == "467315"
    assert validate("otp", {"code": "12"}, today=today) is None
    assert validate("date", {"date": "2026-11-15"}, today=today) == date(2026, 11, 15)
    assert validate("date", {"date": "2025-01-01"}, today=today) is None                                # past
    assert validate("date", {"earliest": True}, today=today) == "earliest"
    assert validate("time_choice", {"time": "14:30"}, today=today, offered={"14:30", "14:45"}) == "14:30"
    assert validate("time_choice", {"time": "16:30"}, today=today, offered={"14:30", "14:45"}) is None   # not offered
    assert validate("dob_name", {"date_of_birth": "1990-03-15", "first_name": "محمد"}, today=today) == \
        {"date_of_birth": date(1990, 3, 15), "first_name": "محمد"}


async def test_garbled_mobile_is_extracted_and_looked_up(rig):
    from runtime.harness.engine import Agent
    agent, llm, mock, out, _ = rig
    mock.on("mssql_get_patient_info", [{"PatientID": 4455, "FirstName": "Mohammed", "DateofBirth": "1990-03-15"}])
    mock.on("api_send_otp_request", {"success": True})
    llm.then('{"mobile": "0548802968", "confidence": "high"}')               # extraction
    llm.then(("mssql_get_patient_info", {"mobileNo": "0548802968"})).then("وش الرمز؟")
    # a self-correction the pattern parser can't read (it handles plain digit words, even "ثمان", on its own)
    await agent.handle("الرقم هو خمسمية وأربعين ... صفر خمسة أربعة ثمانية ثمانية صفر تسعة وعشرين ثمانية وستين")
    assert mock.calls and mock.calls[0][0] == "mssql_get_patient_info" and mock.calls[0][1]["mobileNo"] == "0548802968"
    assert "mobile: 0548802968" in next(m["content"] for m in agent.s.history if m["role"] == "user"
                                        and "mobile:" in (m.get("content") or ""))


async def test_extraction_not_called_when_the_pattern_already_matched(rig):
    agent, llm, mock, out, _ = rig
    mock.on("mssql_get_patient_info", [{"PatientID": 4455, "FirstName": "Mohammed", "DateofBirth": "1990-03-15"}])
    llm.then(("mssql_get_patient_info", {"mobileNo": "0551234567"})).then("تمام")
    await agent.handle("0551234567")
    assert not any("mobile" in (r["messages"][0].get("content") or "")[:60] and "Saudi mobile" in r["messages"][0]["content"]
                   for r in llm.requests)                                    # no extraction round trip


async def test_a_yes_before_the_read_back_does_not_book():
    """The caller's "yes, 8:30" that chose the time must not confirm the booking the model then parks."""
    from runtime.harness.policy import NeedsConfirmation, Policy
    from runtime.harness.session import Session
    from runtime.tools import ToolContext, ToolDef
    s = Session(call_id="c")
    s.auth.verified = True
    book = ToolDef(name="book", skill="x", kind="write", source="mcp", description="", input_schema={},
                   confirm="readback")
    p, ctx, args = Policy(s), ToolContext(call_id="c"), {"DoctorID": 1, "StartTime": "08:30"}
    s.turn_id, s.last_reply = 5, "yes"
    with pytest.raises(NeedsConfirmation):
        await p.pre(book, dict(args), ctx)            # parks it
    with pytest.raises(NeedsConfirmation):
        await p.pre(book, dict(args), ctx)            # same turn, same "yes": still not confirmed
    s.turn_id, s.last_reply = 6, "yes"                # the answer to the read-back
    assert (await p.pre(book, dict(args), ctx))["StartTime"] == "08:30"


# ---------------------------------------------------------------- Hamsa parity: prompt variables, greeting, first speaker


def test_template_variables_are_hamsa_system_variables():
    from runtime.skills.loader import area_code, template_vars
    s = Session(call_id="call-9", ani="+966548802968", agent_name="HMG Care", agent_number="8001")
    v = template_vars(s)
    assert v["call_id"] == "call-9" and v["direction"] == "inbound"
    assert v["user_number"] == v["userNumber"] == "+966548802968" and v["user_number_area_code"] == "54"
    assert v["agent_name"] == "HMG Care" and v["agent_number"] == "8001"
    assert len(v["current_time"]) == 5 and len(v["current_date"]) == 10 and v["current_weekday"]
    assert area_code("0112345678") == "11" and area_code("00966112345678") == "11" and area_code("+14155550123") == ""
    assert template_vars(Session(call_id="x"))["user_number_area_code"] == ""


async def test_dynamic_greeting_is_rendered(rig):
    from runtime.harness.prompts import Phrases
    agent, llm, mock, out, _ = rig
    agent.s.agent_name = "Sara"
    agent.ph = Phrases({"GREETING": {"ar": "أهلاً، أنا {{ agent_name }}", "en": "Hi, this is {{ agent_name }} ({{ call_id }})"}})
    await agent.start()
    lang = agent.s.language.language
    assert out.said == [("أهلاً، أنا Sara" if lang == "ar" else "Hi, this is Sara (call-1)")]
    assert agent.s.history[-1]["content"] == out.said[0]


async def test_plain_greeting_is_unchanged(rig):
    agent, llm, mock, out, _ = rig
    await agent.start()
    assert out.said == [agent.ph.GREETING[agent.s.language.language]]


async def test_wait_for_user_to_speak_first(rig):
    agent, llm, mock, out, _ = rig
    agent.settings = agent.settings.model_copy(update={"voice_wait_for_user": "always"})
    await agent.start()
    assert out.said == [] and agent.s.history == []                       # silent until the caller speaks
    llm.then("Hello, how can I help?")
    await agent.handle("hello")
    assert out.said                                                       # then the agent answers normally


async def test_wait_for_user_outbound_only(rig):
    agent, llm, mock, out, _ = rig
    agent.settings = agent.settings.model_copy(update={"voice_wait_for_user": "outbound"})
    await agent.start()
    assert len(out.said) == 1                                             # inbound: greets as usual
    agent.s.direction = "outbound"
    out.said.clear()
    await agent.start()
    assert out.said == []


def test_wait_for_user_knob_is_validated():
    from runtime.platform.bundle import knob_errors
    assert not knob_errors({"voice_wait_for_user": "always"})
    assert knob_errors({"voice_wait_for_user": "sometimes"})
