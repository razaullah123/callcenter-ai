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


# ---------------------------------------------------------------- a provider failure is not the agent's failure

def test_which_errors_are_the_providers():
    from evals.runner import is_provider_error
    for e in ("APITimeoutError('Request timed out.')", "ReadTimeout('')", "ConnectError('x')", "RateLimitError('429')",
              "InternalServerError('boom')", "APIConnectionError('Connection error.')", "Error code: 503 status_code=503"):
        assert is_provider_error(e), e
    for e in (None, "", "KeyError('x')", "ValueError('bad')", "simulated caller failed: ValueError()"):
        assert not is_provider_error(e), e


def _ran(error=None, passed=None):
    checks = [{"check": "booked", "passed": error is None, "detail": ""}]
    if error:
        checks.append({"check": "no_error", "passed": False, "detail": error})
    return {"case": "t", "error": error, "checks": checks, "passed": passed if passed is not None else error is None}


async def test_a_case_is_played_again_when_the_provider_times_out(monkeypatch):
    import evals.runner as runner
    answers = [_ran("APITimeoutError('Request timed out.')"), _ran("ReadTimeout('')"), _ran()]
    calls = []

    async def once(case, **kw):
        calls.append(1)
        return answers.pop(0)
    monkeypatch.setattr(runner, "_run_case_once", once)
    r = await runner.run_case(Case(id="t", suite="s", title="t", caller={}, expect={}), agent_llm=None, caller_llm=None)
    assert r["passed"] and r["attempts"] == 3 and len(calls) == 3 and "provider_problem" not in r


async def test_a_case_that_never_gets_an_answer_says_the_provider_failed(monkeypatch):
    import evals.runner as runner
    calls = []

    async def once(case, **kw):
        calls.append(1)
        return _ran("APITimeoutError('Request timed out.')")
    monkeypatch.setattr(runner, "_run_case_once", once)
    r = await runner.run_case(Case(id="t", suite="s", title="t", caller={}, expect={}), agent_llm=None, caller_llm=None)
    assert not r["passed"] and r["attempts"] == 3 and len(calls) == 3 and r["provider_problem"] is True
    detail = next(c["detail"] for c in r["checks"] if c["check"] == "no_error")
    assert "failed on all 3 attempts" in detail and "not a verdict on the agent" in detail
    calls.clear()
    r2 = await runner.run_case(Case(id="t", suite="s", title="t", caller={}, expect={}), agent_llm=None, caller_llm=None, attempts=1)
    assert len(calls) == 1 and r2["attempts"] == 1


async def test_real_failures_are_not_retried(monkeypatch):
    import evals.runner as runner
    calls = []

    async def once(case, **kw):
        calls.append(1)
        return _ran("KeyError('x')") if len(calls) == 1 else _ran()
    monkeypatch.setattr(runner, "_run_case_once", once)
    r = await runner.run_case(Case(id="t", suite="s", title="t", caller={}, expect={}), agent_llm=None, caller_llm=None)
    assert not r["passed"] and len(calls) == 1 and "provider_problem" not in r                       # an agent bug counts at once
    plain = {"case": "t", "error": None, "checks": [{"check": "booked", "passed": False, "detail": "x"}], "passed": False}

    async def failing(case, **kw):
        calls.append(1)
        return dict(plain)
    monkeypatch.setattr(runner, "_run_case_once", failing)
    calls.clear()
    r = await runner.run_case(Case(id="t", suite="s", title="t", caller={}, expect={}), agent_llm=None, caller_llm=None)
    assert len(calls) == 1 and not r["passed"]                                                       # a failed check isn't a retry reason


def test_the_gate_shows_the_providers_failure_alone():
    from runtime.platform.gate import failed_lines
    consequences = [{"check": "tools_in_order", "passed": False, "detail": "missing / out of order: ['api_book_Appointment']"},
                    {"check": "booked", "passed": False, "detail": "api_book_Appointment not called"},
                    {"check": "language", "passed": True, "detail": ""},
                    {"check": "no_error", "passed": False, "detail": "the model provider failed on all 3 attempts (ReadTimeout('')) — not a verdict on the agent"}]
    broke = {"checks": consequences, "provider_problem": True}
    assert failed_lines(broke) == ["no_error: the model provider failed on all 3 attempts (ReadTimeout('')) — not a verdict on the agent"]
    ordinary = {"checks": consequences[:2]}                                  # a real failure keeps every failed check
    assert [x.split(":")[0] for x in failed_lines(ordinary)] == ["tools_in_order", "booked"]
    assert failed_lines({"checks": []}) == []


# ---------------------------------------------------------------- the simulated caller waits and retries its own slow replies

class _SlowThenFine:
    def __init__(self, failures, error="APITimeoutError('Request timed out.')"):
        self.failures, self.error, self.calls = failures, error, 0

    async def stream(self, messages, **kw):
        from runtime.providers import TextDelta
        self.calls += 1
        if self.calls <= self.failures:
            raise RuntimeError(self.error)
        yield TextDelta('{"say": "Dermatology, please"}')


async def test_the_simulated_caller_retries_a_slow_reply_itself():
    import pytest

    from evals.caller import LLMCaller
    llm = _SlowThenFine(2)
    caller = LLMCaller(llm, language="en", persona="", goal="book", facts={})
    assert await caller.next("Which clinic?") == "Dermatology, please" and llm.calls == 3          # two timeouts, then an answer
    assert sum("retrying after" in r for r in caller.raw) == 2
    dead = LLMCaller(_SlowThenFine(5), language="en", persona="", goal="book", facts={})
    with pytest.raises(RuntimeError):                                                              # three timeouts in a row: give up
        await dead.next("Which clinic?")
    broken = LLMCaller(_SlowThenFine(1, "KeyError('x')"), language="en", persona="", goal="book", facts={})
    with pytest.raises(RuntimeError):                                                              # not a provider error: no retry
        await broken.next("Which clinic?")


async def test_test_tooling_gets_a_longer_time_limit_than_a_live_call(monkeypatch):
    from types import SimpleNamespace
    import evals.runner as runner
    seen = []
    monkeypatch.setattr(runner, "create", lambda kind, name, cfg=None: seen.append(cfg or {}) or SimpleNamespace())
    await runner.run_suite([], agent_llm=SimpleNamespace(settings=SimpleNamespace(model="m")), use_judge=True)
    caller, judge = seen
    assert caller["timeout_s"] == judge["timeout_s"] == runner.TOOLING_TIMEOUT_S >= 60            # a live call's model waits 10 s
