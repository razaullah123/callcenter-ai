"""The eval harness itself (offline: scripted agent LLM + scripted caller + fixture HIS)."""

from datetime import timedelta

from evals.checks import DIAGNOSIS, FEMININE, MASCULINE, check_all
from evals.fixtures import fixture_backend
from evals.runner import Case, load_cases, run_case, summarize
from runtime.data.reference import set_reference
from runtime.harness.nlu.dates import today_riyadh

from .test_harness import ScriptedLLM
from .test_local_tools import REF


def _result(**kw):
    base = {"transcript": [], "tool_calls": [], "turns": 3, "session": {"verified": True, "handoff": None, "slots": {}}}
    return {**base, **kw}


def test_checks_tool_order_and_forbidden():
    r = _result(tool_calls=[("a", {}), ("x", {}), ("b", {}), ("c", {})])
    ok = {c["check"]: c["passed"] for c in check_all({"tools_called": ["a", "b", "c"], "tools_not_called": ["z"]}, r)}
    assert ok == {"tools_in_order": True, "tools_not_called": True, "no_medical_advice": True}
    bad = {c["check"]: c["passed"] for c in check_all({"tools_called": ["c", "a"]}, r)}
    assert bad["tools_in_order"] is False


def test_gender_and_diagnosis_patterns():
    assert FEMININE.search("وش تبين اليوم؟") and not FEMININE.search("وش تبي اليوم؟")
    assert MASCULINE.search("حاب تحجز؟") and not MASCULINE.search("حابة تحجزين؟")
    assert DIAGNOSIS.search("عندك التهاب في الحلق") and DIAGNOSIS.search("take some ibuprofen")
    assert not DIAGNOSIS.search("أنصحك بعيادة الأنف والأذن")


def test_language_check():
    r = _result(transcript=[{"role": "agent", "text": "مرحباً"}, {"role": "agent", "text": "Sure, what's your number?"}])
    [lang] = [c for c in check_all({"language": "ar", "no_medical_advice": False}, r) if c["check"] == "language"]
    assert not lang["passed"]


async def test_fixture_slots_are_in_the_future():
    mock = fixture_backend()
    ok, data = await mock.call("mssql_get_TopFive_nearestClinic_have_doctorSlots", {"projectId": 12, "clinicId": 5}, 5)
    days = {k for d in data["clinics"] for k, v in d.items() if isinstance(v, list)}
    assert ok and days <= {(today_riyadh() + timedelta(days=n)).isoformat() for n in (1, 2)}
    ok, data = await mock.call("mssql_get_patient_info", {"mobileNo": "0550000002"}, 5)
    assert data["count"] == 2


def test_all_case_files_load():
    cases = load_cases()
    assert len(cases) >= 15 and {c.suite for c in cases} >= {"auth", "booking", "safety"}
    assert len({c.id for c in cases}) == len(cases)


async def test_run_case_end_to_end_scripted():
    set_reference(REF)
    agent = ScriptedLLM()
    # lookup, OTP send and verification are done by the harness from the parsed mobile / code;
    # the model only speaks.
    agent.then("عطني رقم جوالك المسجل لو سمحت.") \
         .then("أرسلت لك الرمز على الواتساب، وش الرمز؟") \
         .then("هلا محمد، وش تبي اليوم؟")
    case = Case(id="t", suite="auth", title="t", max_turns=5,
                caller={"language": "ar", "script": ["ابي موعد", "0551234567", "1234"]},
                expect={"verified": True, "tools_called": ["mssql_get_patient_info", "api_send_otp_request",
                                                           "api_verify_otp"], "gender": "male", "language": "ar"})
    r = await run_case(case, agent_llm=agent, caller_llm=None)
    assert r["passed"], r["checks"]
    assert r["session"]["gender"] == "male"          # from the fixture patient record
    assert [t for t, _ in r["tool_calls"]] == ["mssql_get_patient_info", "api_send_otp_request", "api_verify_otp"]
    s = summarize([r])
    assert s["passed"] == 1 and s["checks"]["verified"] == {"passed": 1, "total": 1}


def test_caller_reply_keeps_one_utterance():
    from evals.caller import END, first_utterance
    assert first_utterance('We need to answer. {"say": "Al Olaya, Riyadh"}{"say": "Dermatology"}') == "Al Olaya, Riyadh"
    assert first_utterance("Al Olaya, RiyadhWe need to book dermatology") == "Al Olaya, Riyadh"
    assert first_utterance("Accept Dr. Rubeena please.") == "Accept Dr. Rubeena please."
    assert first_utterance("العليا.أبغى دكتور ثاني.[END]") == "العليا."
    assert first_utterance('{"say": "[END]"}') == END
    assert first_utterance("نعم [END]") == "نعم"


def test_checks_which_doctor_was_booked():
    book = ("api_book_Appointment", {"DoctorID": "12565"})
    offered = [[5018, 12565, 777]]
    declined = check_all({"booked_first_doctor": False, "booked_offered_doctor": True},
                         _result(tool_calls=[book], offered_doctors=offered))
    assert all(c["passed"] for c in declined)
    took_first = check_all({"booked_first_doctor": False}, _result(tool_calls=[("api_book_Appointment", {"DoctorID": 5018})],
                                                                   offered_doctors=offered))
    assert not took_first[0]["passed"] and "first suggested 5018" in took_first[0]["detail"]
    invented = check_all({"booked_offered_doctor": True}, _result(tool_calls=[("api_book_Appointment", {"DoctorID": 1})],
                                                                  offered_doctors=offered))
    assert not invented[0]["passed"]
    not_booked = check_all({"booked_first_doctor": True}, _result(tool_calls=[], offered_doctors=offered))
    assert not not_booked[0]["passed"]


async def test_caller_retries_groq_parse_failure():
    from evals.caller import LLMCaller
    from runtime.providers import TextDelta

    class Flaky:
        calls = 0

        async def stream(self, messages, **_):
            Flaky.calls += 1
            if Flaky.calls == 1:
                raise RuntimeError("Parsing failed. The model generated output that could not be parsed. "
                                   "See 'failed_generation' for more details.")
            yield TextDelta('{"say": "العليا"}{"say": "لا ما ابي"}')

    caller = LLMCaller(Flaky(), language="ar", persona="", goal="", facts={})
    assert await caller.next("وش المستشفى؟") == "العليا"
    assert caller.raw[0].startswith("<groq parse error")


def test_agent_treats_groq_parse_failure_as_retryable():
    from runtime.harness.engine import _tool_call_error
    assert _tool_call_error(RuntimeError("Parsing failed. The model generated output that could not be parsed.")) \
        == "malformed tool call"
    assert _tool_call_error(RuntimeError("rate limit")) is None
