"""Voice layer: turn detection (with speculative pause), TTS normalization, speech player."""

import asyncio

import numpy as np
import pytest

from runtime.events import EventBus
from runtime.voice.normalize import clock_words, normalize_for_tts, number_words
from runtime.voice.player import AudioFormat, SpeechPlayer
from runtime.voice.turn import TurnConfig, TurnDetector


class FakeVAD:
    """Speech whenever the window's mean amplitude is high."""

    def reset(self):
        pass

    def __call__(self, window):
        return 0.9 if np.abs(window).mean() > 0.05 else 0.05


def tone(ms):
    t = np.arange(16 * ms) / 16000
    return (np.sin(2 * np.pi * 220 * t) * 0.5 * 32767).astype(np.int16).tobytes()


def silence(ms):
    return b"\x00\x00" * 16 * ms


def run(det, audio):
    events = []
    for i in range(0, len(audio), 640):
        events += [e for e, _ in det.feed(audio[i:i + 640])]
    return events


def test_turn_start_pause_end():
    det = TurnDetector(TurnConfig(end_silence_ms=544, pause_ms=256), vad=FakeVAD())
    assert run(det, silence(300) + tone(800) + silence(700)) == ["start", "pause", "end"]


def test_pause_then_resume_is_one_utterance():
    det = TurnDetector(TurnConfig(end_silence_ms=544, pause_ms=256), vad=FakeVAD())
    events = run(det, tone(600) + silence(350) + tone(600) + silence(700))
    assert events == ["start", "pause", "resume", "pause", "end"]


def test_short_blip_is_noise_not_turn():
    det = TurnDetector(TurnConfig(min_speech_ms=64, min_utterance_ms=250), vad=FakeVAD())
    assert "end" not in run(det, tone(120) + silence(800))


@pytest.mark.parametrize("text,lang,expected", [
    ("موعدك الساعة 08:15", "ar", "موعدك الساعة ثمان وربع صباحاً"),
    ("الساعة 15:30", "ar", "الساعة ثلاث ونص مساءً"),
    ("عندك 3 مواعيد", "ar", "عندك ثلاثة مواعيد"),
    ("رقم الموعد 222846659", "ar", "رقم الموعد اثنين، اثنين، اثنين، ثمانية، أربعة، ستة، ستة، خمسة، تسعة"),
    ("الذي ينتهي بـ 2968", "ar", "الذي ينتهي بـ: اثنين، تسعة، ستة، ثمانية"),
    ("الأحد الساعة 8 صباحاً، 8:15 صباحاً، و8:30 صباحاً.", "ar",
     "الأحد الساعة ثمان صباحاً، ثمان وربع صباحاً، وثمان ونص صباحاً."),
    ("الثلاثاء الساعة 2:30 مساءً، و3 مساءً", "ar", "الثلاثاء الساعة ثنتين ونص مساءً، وثلاث مساءً"),
    ("يوم 15 نوفمبر 2026", "ar", "يوم خمسطعش نوفمبر ألفين وستة وعشرين"),
    ("via whatsapp that ends with 2968", "en", "via whatsapp that ends with two, nine, six, eight"),
    ("Sunday at 8 AM, 8:15 AM and 8:30 AM.", "en", "Sunday at eight a.m., eight fifteen a.m. and eight thirty a.m."),
    ("at 2:05 PM and 12 PM", "en", "at two oh five p.m. and twelve p.m."),
    ("Your appointment is at 08:15", "en", "Your appointment is at eight fifteen a.m."),
    ("on 15 November 2026, 3 slots", "en", "on 15 November 2026, 3 slots"),
])
def test_tts_normalization(text, lang, expected):
    assert normalize_for_tts(text, lang) == expected


def test_number_and_clock_words():
    assert number_words(2026) == "ألفين وستة وعشرين"
    assert clock_words(10, 45) == "إحدعش إلا ربع صباحاً"


class FakeTTS:
    def __init__(self, ms_per_char=5):
        self.calls = []
        self.ms_per_char = ms_per_char

    async def synthesize(self, text, *, language="ar", voice=None, encoding="pcm16", sample_rate=None):
        from runtime.providers import AudioChunk
        self.calls.append(text)
        for _ in range(2):                                  # streamed in two chunks
            await asyncio.sleep(0.005)
            yield AudioChunk(b"\x01\x00" * 16 * (len(text) * self.ms_per_char // 2), 16000, "pcm16")


async def test_player_plays_in_order_and_reports_heard_on_interrupt():
    bus = EventBus()
    await bus.start()
    sent, events = [], []

    async def send_audio(b):
        sent.append(len(b))

    async def send_event(m):
        events.append(m)

    player = SpeechPlayer(FakeTTS(ms_per_char=20), AudioFormat("pcm16", 16000), send_audio, send_event,
                          bus.bind(call_id="t"), lead_ms=0)
    await player.say("الجملة الأولى قصيرة.", language="ar")
    await player.say("والجملة الثانية أطول شوي من الأولى بكثير.", language="ar")
    assert player.active
    await asyncio.sleep(0.6)            # first sentence (~0.4 s) fully played, second under way
    heard = await player.interrupt()
    assert heard.startswith("الجملة الأولى قصيرة.") and "{'event': 'clear'}" in str(events)
    assert not player.active
    await player.say("بعد المقاطعة.", language="ar")
    await player.drained()
    assert not player.active and sum(sent) > 0
    await player.close()
    await bus.stop()


# ---------------------------------------------------------------- noise vs speech, confirmed barge-in

def test_noise_transcripts_are_dropped():
    from runtime.voice.stt_quality import noise_reason
    seg = lambda ns, lp: {"segments": [{"no_speech_prob": ns, "avg_logprob": lp}]}   # noqa: E731
    assert noise_reason(".", {}) == "hallucination"
    assert noise_reason("Thank you.", {}) == "thanks_on_noise"                   # no confidence → don't trust it
    assert noise_reason("Thank you.", seg(0.4, -0.5)) == "thanks_on_noise"
    assert noise_reason("Thank you.", seg(0.02, -0.2)) is None                   # clearly spoken: keep
    assert noise_reason("I want to take this picture", seg(0.8, -1.4)) == "no_speech"
    assert noise_reason("0551234567", seg(0.01, -0.1)) is None
    assert noise_reason("ابي احجز موعد", seg(0.05, -0.3)) is None


class _Player:
    def __init__(self, saying):
        self.active, self.saying, self.interrupted = True, saying, False

    def recent_text(self):
        return self.saying

    async def interrupt(self):
        self.interrupted, self.active = True, False
        return self.saying[:10]


class _Ev:
    def __init__(self):
        self.events = []

    def emit(self, type_, **data):
        self.events.append((type_, data))


def _call(saying, heard_text, raw=None):
    from runtime.providers import Transcript
    from runtime.voice.call import VoiceCall
    call = VoiceCall.__new__(VoiceCall)
    call.player, call.ev = _Player(saying), _Ev()
    call.agent = type("A", (), {"on_interrupted": lambda self, heard: None})()
    call._agent_task, call._barged, call._barge_check = None, False, None
    call._speech_ms, call._next_barge_ms = 320, 300
    call.levels, call.session = None, type("S", (), {"language": type("L", (), {"decided": True, "language": "en"})()})()

    async def transcribe(pcm):
        return Transcript(heard_text, "ar", 400, raw or {"segments": [{"no_speech_prob": 0.02, "avg_logprob": -0.2}]})
    call._transcribe = transcribe
    return call


async def test_barge_in_needs_real_words_not_echo_or_noise():
    real = _call("I've sent a verification code to your WhatsApp.", "wait, I didn't get it")
    await real._confirm_barge_in(b"x")
    assert real.player.interrupted and real._barged

    echo = _call("I've sent a verification code to your WhatsApp.", "sent a verification code")
    await echo._confirm_barge_in(b"x")
    assert not echo.player.interrupted and echo._next_barge_ms == 320 + 600
    assert echo.ev.events[-1][1]["why"] == "echo"

    noise = _call("I've sent a verification code to your WhatsApp.", "Thank you.",
                  {"segments": [{"no_speech_prob": 0.5, "avg_logprob": -0.9}]})
    await noise._confirm_barge_in(b"x")
    assert not noise.player.interrupted and noise.ev.events[-1][1]["why"] == "thanks_on_noise"


def test_noise_rule_matches_groq_probe():
    # measured on Groq whisper-large-v3 (2026-09-28): white noise → "Thank you." (0.755, -0.746); real speech ≤ 0.02
    from runtime.voice.stt_quality import noise_reason
    assert noise_reason("Hello there", {"segments": [{"no_speech_prob": 0.755, "avg_logprob": -0.746}]}) == "no_speech"
    assert noise_reason("Hello there", {"segments": [{"no_speech_prob": 0.018, "avg_logprob": -0.409}]}) is None


def test_prompt_echo_and_level_gate():
    from runtime.platform.bundle import DEFAULT_STT_HINT as STT_HINT
    from runtime.voice.stt_quality import LevelGate, echoes_prompt, noise_reason, speech_level_db
    assert noise_reason("Appointment, clinic, Olaya, Al Hamra, Arryan.", {}, STT_HINT["en"]) == "prompt_echo"
    assert not echoes_prompt("Olaya", STT_HINT["en"]) and not echoes_prompt("مستشفى العليا", STT_HINT["ar"])
    assert echoes_prompt("موعد عيادة مستشفى العليا الحمراء الريان", STT_HINT["ar"])
    loud = (np.sin(np.arange(16000) / 16000 * 2 * np.pi * 200) * 8000).astype(np.int16).tobytes()
    quiet = (np.frombuffer(loud, np.int16) // 10).astype(np.int16).tobytes()      # -20 dB: a voice across the room
    gate = LevelGate(margin_db=12)
    assert not gate.too_quiet(speech_level_db(quiet))                               # nothing learned yet
    gate.accept(speech_level_db(loud)); gate.accept(speech_level_db(loud))
    assert gate.too_quiet(speech_level_db(quiet)) and not gate.too_quiet(speech_level_db(loud))


def test_overlap_with_agent_voice_is_strict():
    # live call 3: invented "Thank you." at no_speech 0.12–0.14 while / right after the agent spoke
    from runtime.voice.stt_quality import noise_reason
    seg = lambda ns: {"segments": [{"no_speech_prob": ns, "avg_logprob": -0.5}]}   # noqa: E731
    assert noise_reason("Thank you.", seg(0.126)) == "thanks_on_noise"
    assert noise_reason("Thank you.", seg(0.02), overlap=True) == "thanks_on_noise"   # never during the agent's voice
    assert noise_reason("We invite the Sulaiman to listen.", seg(0.084), overlap=True) == "overlap_unsure"
    assert noise_reason("The code is 866089", seg(0.002), overlap=True) is None
    assert noise_reason("wait, not that one", {}, overlap=True) is None     # no confidence reported: don't guess


async def test_hangup_cancels_every_background_task():
    from runtime.harness.session import Session
    from runtime.voice.call import VoiceCall

    async def forever():
        await asyncio.sleep(3600)

    call = VoiceCall.__new__(VoiceCall)
    call.session, call.aec, call.ev = Session(call_id="c"), None, _Ev()
    call.player = type("P", (), {"close": lambda self: asyncio.sleep(0)})()
    call._stopped, call.end_reason, call._forced_reason = False, "in_progress", None
    tasks = [asyncio.create_task(forever()) for _ in range(4)]
    call._agent_task, call._speculative, call._barge_check, call._stt_tasks = tasks[0], tasks[1], tasks[2], {tasks[3]}
    await call.stop()
    await asyncio.sleep(0)
    assert all(t.cancelled() for t in tasks) and call.end_reason == "caller_hangup"
    await call.on_audio(b"\x00" * 640)                  # audio after the hang-up is ignored (no VAD / STT)
    await call.stop()                                   # idempotent


async def test_end_call_from_console():
    from runtime.harness.session import Session
    from runtime.voice.call import VoiceCall
    sent, closed = [], []

    async def send_event(msg):
        sent.append(msg)

    async def close_transport():
        closed.append(True)

    call = VoiceCall.__new__(VoiceCall)
    call.session, call.aec, call.ev, call.player = Session(call_id="c"), None, _Ev(), _Player("some reply")
    call._stopped, call.end_reason, call._forced_reason = False, "in_progress", None
    call._agent_task = call._speculative = call._barge_check = None
    call._stt_tasks, call._send_event, call.close_transport = set(), send_event, close_transport
    call.player.close = lambda: asyncio.sleep(0)
    await call.end_from_console()
    assert call.player.interrupted and sent == [{"event": "hangup"}] and closed == [True]
    await call.stop()                                  # the transport's cleanup then stops the call
    assert call.end_reason == "ended_from_console"


async def test_agent_call_limits(monkeypatch):
    """Per-agent call settings: no interruptions, the inactivity check-in, and the maximum call length."""
    from runtime.harness.prompts import Phrases
    from runtime.harness.session import Session
    from runtime.platform.bundle import knob_errors
    from runtime.voice.call import VoiceCall
    assert not knob_errors({"voice_interrupt": False, "voice_vad_threshold": 0.6, "voice_inactivity_s": 15,
                            "call_max_minutes": 10})
    call = VoiceCall.__new__(VoiceCall)
    call.interrupt, call.barge_in_grace_ms, call.aec = False, 0, None
    call.player = type("P", (), {"speaking_since": None})()
    assert call._barge_in_allowed() is False                       # this agent always finishes what it says

    said, sent, closed = [], [], []

    async def say(text):
        said.append(text)

    async def send_event(msg):
        sent.append(msg)

    async def close_transport():
        closed.append(True)

    call = VoiceCall.__new__(VoiceCall)
    call.session, call.ev = Session(call_id="c"), _Ev()
    call.player = type("P", (), {"active": False})()
    call.turns = type("T", (), {"in_speech": False})()
    call.agent = type("A", (), {"ph": Phrases(), "_say": staticmethod(say)})()
    call._stopped, call._forced_reason, call._agent_task = False, None, None
    call._send_event, call.close_transport = send_event, close_transport
    call.inactivity_s, call.max_call_s = 0.6, 2.5
    import time
    call._last_activity = time.monotonic()
    await asyncio.wait_for(call._watch_limits(), 5)
    en = Phrases()
    assert said[0] == en.STILL_THERE["ar"] and said[-1] == en.CALL_TIME_LIMIT["ar"]
    assert said.count(en.STILL_THERE["ar"]) == 1                   # once per silence, not every half second
    assert sent == [{"event": "hangup"}] and closed == [True] and call._forced_reason == "max_duration"


async def test_max_call_duration_cuts_in_mid_answer():
    """Time is up while the agent is still answering: the answer stops, the closing line plays, the call ends."""
    from runtime.harness.prompts import Phrases
    from runtime.harness.session import Session
    from runtime.voice.call import VoiceCall
    said, sent, heard = [], [], []

    async def say(text):
        said.append(text)

    async def send_event(msg):
        sent.append(msg)

    async def long_answer():
        await asyncio.sleep(3600)

    call = VoiceCall.__new__(VoiceCall)
    call.session, call.ev = Session(call_id="c"), _Ev()
    call.session.language.language = "en"
    call.player = _Player("Your appointment options are")          # the agent is speaking
    call.turns = type("T", (), {"in_speech": False})()
    call.agent = type("A", (), {"ph": Phrases(), "_say": staticmethod(say), "on_interrupted": lambda self, h: heard.append(h)})()
    call._stopped, call._forced_reason = False, None
    call._agent_task = asyncio.create_task(long_answer())
    call._send_event, call.close_transport = send_event, None
    call.inactivity_s, call.max_call_s = 0, 0.6
    await asyncio.wait_for(call._watch_limits(), 5)
    assert call.player.interrupted and heard and call._agent_task.cancelled()
    assert said == [Phrases().CALL_TIME_LIMIT["en"]] and sent == [{"event": "hangup"}]
    assert call._forced_reason == "max_duration"
