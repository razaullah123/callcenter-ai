"""Harness policies, enforced in code (not only in the prompt).

Pre-hooks (before a tool runs):
  • auth gate — nothing but authentication tools until the caller is OTP-verified
  • argument normalization — mobile → 05XXXXXXXX, OTP → digits
  • confirmation gate — `affirm`: caller's latest reply must be a yes (or, for sends, an explicit request);
                        `readback`: the call is parked as a pending action; the harness executes it when the
                        caller says yes after the agent read the details back
Post-hooks (after a tool runs):
  • auth bookkeeping — patient lookup → candidates (PHI hidden from the LLM), OTP sent / verified
  • tool-specific bookkeeping / grounding — named hooks from the tool policy (packs)
Output filter: strips formatting the TTS would read aloud.
"""

import json
import re
import time
from typing import Any

from runtime.text.arabic import normalize
from runtime.tools import ToolContext, ToolDef, ToolError, ToolResult

from .nlu.dates import resolve_date, resolve_dob
from .nlu.gender import gender_from_name
from .nlu.numbers import extract_code, normalize_mobile
from runtime.tools.hooks import run_tool_hooks

from .session import VERIFY_SKILL, PendingAction, Session

MAX_OTP_ATTEMPTS = 3
MAX_MATCH_ATTEMPTS = 2
OTP_RESEND_AFTER_S = 45     # a second WhatsApp code within this window invalidates the one the caller is reading

_CHANNEL_WORDS = re.compile(r"(واتس|whats|رساله|sms|مسج|ايميل|email|بريد)", re.IGNORECASE)


class NeedsConfirmation(ToolError):
    pass


class Policy:
    """Harness rules around every tool call. Tools are known only by their policy: a role the harness drives
    (identity.lookup / identity.send_code / identity.verify_code), confirmation mode, the claim they back, and
    named hooks (runtime.tools.hooks, defined in packs) for anything tool-specific."""

    def __init__(self, session: Session, catalog=None) -> None:
        self.s = session
        self.catalog = catalog

    def _role_tool(self, role: str) -> str:
        t = self.catalog.by_role(role) if self.catalog is not None else None
        return t.name if t else role

    # ---------------- pre-hooks ----------------

    async def pre(self, tool: ToolDef, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        s = self.s
        if not s.auth.verified and tool.skill != VERIFY_SKILL:
            raise ToolError("the caller must be verified (mobile number + OTP) before anything else")

        if tool.role == "identity.lookup":
            arg = tool.role_args.get("mobile", "mobileNo")
            # The caller's own words first; the model's value only when the number was spread over turns.
            mobile = normalize_mobile(s.last_user_text) or normalize_mobile(str(args.get(arg, "")))
            if not mobile:
                raise ToolError("the mobile number is incomplete; ask the caller to repeat it (10 digits starting 05)")
            args = {k: v for k, v in args.items() if k not in ("patientId",)}
            args[arg] = mobile
        elif tool.role == "identity.verify_code":
            arg = tool.role_args.get("code", "Otp")
            code = extract_code(str(args.get(arg, ""))) or extract_code(s.last_user_text)
            if not code:
                raise ToolError("the code is incomplete; ask the caller to repeat the code digit by digit")
            args[arg] = code
        elif tool.role == "identity.send_code":
            arg = tool.role_args.get("channel", "Channel")
            args.setdefault(arg, tool.role_args.get("default_channel", "WhatsApp"))
            if s.memory.get("otp_sent") == (s.turn_id, args[arg]):
                raise ToolError("the code was already sent this turn — just ask the caller for it")
            sent_at = s.memory.get("otp_sent_at", {}).get(args[arg])
            if sent_at and time.monotonic() - sent_at < OTP_RESEND_AFTER_S:
                raise ToolError(f"a code was sent by {args[arg]} {int(time.monotonic() - sent_at)} s ago — ask "
                                "the caller for it; resend only by SMS if they say it didn't arrive")

        args = normalize_datetime_args(args)
        # grounding and other tool-specific checks (named hooks, e.g. only offered hospitals / slots)
        args = run_tool_hooks("pre", tool, args, s, ctx=ctx)
        if ctx.extra.get("confirmed_action") == tool.name:
            return args
        if tool.confirm == "readback":
            pending = s.pending_action
            # the yes must answer the read-back: a later turn than the one that parked it (a "yes, 8:30" that
            # chose the time is not a confirmation — live call 2026-09-29 booked without any read-back)
            if pending and pending.tool == tool.name and pending.args == _llm_view(args) and s.last_reply == "yes"                     and s.turn_id > pending.turn_id:
                return args
            s.pending_action = PendingAction(tool.name, _llm_view(args), s.turn_id)
            raise NeedsConfirmation(
                "confirmation required: in one short sentence, in the future tense, read the details back and ask "
                "the caller whether to go ahead (e.g. 'بحجز لك ... أحجزه لك؟'). Do not say it is done. Do NOT call "
                "this tool again — the system completes it automatically when the caller says yes.")
        if tool.confirm == "affirm":
            explicit_send = tool.kind == "send" and _CHANNEL_WORDS.search(normalize(s.last_user_text))
            if s.last_reply != "yes" and not explicit_send:
                raise ToolError("ask the caller to confirm this action first, then call the tool after they say yes")
            s.last_reply = "consumed"   # one "yes" authorizes one action
        return args

    # ---------------- post-hooks ----------------

    async def post(self, tool: ToolDef, args: dict[str, Any], result: ToolResult, ctx: ToolContext) -> None:
        if tool.kind in ("write", "send") and result.ok and isinstance(result.data, dict) \
                and result.data.get("success") is False:
            # e.g. "you have another appointment in the same time": nothing was done — never count it as done
            result.ok = False
            result.error = str(result.data.get("message") or "the hospital system refused the request")[:300]
        role = {"identity.lookup": self._after_identity_lookup, "identity.send_code": self._after_identity_send_code,
                "identity.verify_code": self._after_identity_verify_code}.get(tool.role or "")
        if role:
            role(args, result, ctx, tool)
        run_tool_hooks("post", tool, args, self.s, result=result, ctx=ctx)
        self._audit(tool, args, result, ctx)
        if tool.kind in ("write", "send") or not result.ok:
            self.s.tool_failures = 0 if result.ok else self.s.tool_failures + 1

    def _after_identity_lookup(self, args, result: ToolResult, ctx: ToolContext, tool: ToolDef) -> None:
        a = self.s.auth
        a.mobile_no = args.get(tool.role_args.get("mobile", "mobileNo"))
        a.lookup_attempts += 1
        records = patient_records(result.data) if result.ok else []
        a.candidates = records
        send = self._role_tool("identity.send_code")
        if len(records) == 1:
            self._select(records[0], ctx)
            result.content = _j({"records_found": 1, "next": f"send the OTP with {send} (WhatsApp)"})
        elif len(records) > 1:
            result.content = _j({"records_found": len(records), "next": "ask the caller for their date of birth "
                                 "and first name, then call select_patient. Never read out any record details."})
        else:
            result.content = _j({"records_found": 0, "next": "tell the caller no file is registered with this "
                                 "number and ask them to contact the support team to register first"})
        result.data = {"records_found": len(records)}   # keep PHI out of logs / history

    def _after_identity_send_code(self, args, result: ToolResult, ctx: ToolContext, tool: ToolDef) -> None:
        if result.ok:
            channel = args.get(tool.role_args.get("channel", "Channel"), "WhatsApp")
            self.s.auth.otp_sent = True
            self.s.auth.otp_channel = channel
            self.s.memory["otp_sent"] = (self.s.turn_id, channel)
            self.s.memory.setdefault("otp_sent_at", {})[channel] = time.monotonic()
            result.content = _j({"sent": True, "channel": channel,
                                 "next": f"tell the caller a verification code was sent by {channel} and ask for it"})

    def _after_identity_verify_code(self, args, result: ToolResult, ctx: ToolContext, tool: ToolDef) -> None:
        a = self.s.auth
        if result.ok and otp_success(result.data):
            a.verified = True
            ctx.patient_id = a.patient_id
            self.s.memory["identity_asked"] = True
            result.content = _j({"verified": True, "next": "the system asks the caller to confirm their name"})
        else:
            a.otp_attempts += 1
            left = MAX_OTP_ATTEMPTS - a.otp_attempts
            result.ok = False
            result.error = "otp_invalid"
            send = self._role_tool("identity.send_code")
            result.content = _j({"verified": False, "attempts_left": max(left, 0),
                                 "next": "tell the caller the code is incorrect and ask them to repeat it, or offer "
                                         f"to resend it by SMS ({send} with Channel SMS)"
                                 if left > 0 else "attempts exhausted: transfer to a human agent"})
        result.data = {"verified": a.verified}

    def _audit(self, tool: ToolDef, args: dict[str, Any], result: ToolResult, ctx: ToolContext) -> None:
        """PDPL audit: record every call that touches a patient's data (patient id injected) or changes / sends
        something. Kept in its own table, not in the general logs."""
        patient_id = next((args[k] for k in ("PatientID", "PatientId", "patientId") if k in args), None)
        if patient_id is None and tool.kind == "read" and tool.role != "identity.lookup":
            return
        from runtime.control.audit import AUDIT
        AUDIT.record(call_id=self.s.call_id, tool=tool.name, kind=tool.kind,
                     patient_id=patient_id if patient_id is not None else self.s.auth.patient_id,
                     ok=result.ok, confirmed=ctx.extra.get("confirmed_action") == tool.name,
                     verified=self.s.auth.verified, detail=None if result.ok else (result.error or "")[:200])

    # ---------------- patient selection (harness tool) ----------------

    def select_patient(self, first_name: str, date_of_birth: str, ctx: ToolContext) -> dict[str, Any]:
        a = self.s.auth
        a.match_attempts += 1
        dob = resolve_dob(date_of_birth) or resolve_dob(self.s.last_user_text)
        name = normalize(first_name)
        by_dob = [r for r in a.candidates if dob and r.get("dob") == dob.isoformat()]
        # HIS files often hold only the English name: compare across scripts by consonant skeleton.
        matches = [r for r in by_dob if name and any(n.split() and _first_token_match(name, n) for n in r["names"])]
        if len(matches) == 1:
            self._select(matches[0], ctx)
            return {"matched": True, "next": f"send the OTP with {self._role_tool('identity.send_code')} (WhatsApp)"}
        left = MAX_MATCH_ATTEMPTS - a.match_attempts
        return {"matched": False, "attempts_left": max(left, 0),
                "next": "ask the caller to repeat their date of birth (day, month, year) and first name"
                if left > 0 else "attempts exhausted: transfer to a human agent"}

    def _select(self, record: dict[str, Any], ctx: ToolContext) -> None:
        a = self.s.auth
        a.patient_id = record["patient_id"]
        english = self.s.language.language == "en"
        a.first_name = (record.get("first_name_en") if english else record.get("first_name_ar")) \
            or record.get("first_name")
        a.full_name = (record.get("full_name_en") if english else record.get("full_name_ar")) \
            or record.get("full_name_en") or record.get("full_name_ar") or a.first_name
        ctx.patient_id = a.patient_id   # lets the send-code tool inject the patient id
        if record.get("gender"):
            # The file's gender (0.8): above the neutral threshold; the caller's own words (0.9) still win.
            self.s.gender.observe((record["gender"], 0.8), "patient_record")
        else:
            self.s.gender.observe(gender_from_name(a.first_name), "patient_name")


# ---------------- argument normalization ----------------

DATE_PARAMS = {"date", "StrAppointmentDate", "AppointmentDate", "appointmentDate", "dateFrom", "dateTo"}
TIME_PARAMS = {"StartTime", "AppoinmentTime", "dateTimeStart", "dateTimeEnd"}
_TIME = re.compile(r"^\s*(\d{1,2})(?::(\d{2}))?\s*(a\.?\s?m\.?|p\.?\s?m\.?|صباحاً|صباحا|الصبح|ص|مساءً|مساءا|مساء|"
                   r"المساء|الظهر|العصر|المغرب|بالليل|م)?\s*$", re.IGNORECASE)
_PM_WORDS = ("pm", "م", "مساء", "مساءً", "مساءا", "المساء", "الظهر", "العصر", "المغرب", "بالليل")
_AM_WORDS = ("am", "ص", "صباحا", "صباحاً", "الصبح")


def normalize_datetime_args(args: dict[str, Any]) -> dict[str, Any]:
    """Dates → YYYY-MM-DD (the HIS silently returns nothing for DD/MM/YYYY), times → HH:MM (24h)."""
    out = dict(args)
    for k, v in args.items():
        if k in DATE_PARAMS and isinstance(v, str) and v.strip():
            d = resolve_date(v)
            if d:
                out[k] = d.isoformat()
        elif k in TIME_PARAMS and isinstance(v, str) and (m := _TIME.match(v.split("(")[0].strip())):
            h, mnt, ampm = int(m[1]), int(m[2] or 0), (m[3] or "").lower().replace(".", "").replace(" ", "")
            if ampm in _PM_WORDS and h < 12:
                h += 12
            if ampm in _AM_WORDS and h == 12:
                h = 0
            if 0 <= h < 24 and 0 <= mnt < 60:
                out[k] = f"{h:02d}:{mnt:02d}"
    return out


# ---------------- output filter ----------------

_MD = re.compile(r"[*_#`>|~]+|^\s*[-•]\s+|\[(.*?)\]\((.*?)\)", re.MULTILINE)
_EMOJI = re.compile("[\U0001F300-\U0001FAFF\U00002700-\U000027BF\U0001F000-\U0001F2FF\uFE00-\uFE0F\u20E3]")


# "It's done" claims. A tool's policy says which claim a success makes true (`backs`).
_CLAIMS = [
    ("booked", re.compile(r"تم (ال)?حجز|حجزت لك|\b(is|has been|have been|was) booked\b|\bI('ve| have) booked\b",
                          re.IGNORECASE)),
    ("confirmed", re.compile(r"تم (ال)?تأكيد|تم (ال)?تاكيد|أكدت لك|\b(is|has been|was) confirmed\b|"
                             r"\bI('ve| have) confirmed\b", re.IGNORECASE)),
    ("cancelled", re.compile(r"تم (ال)?إلغاء|تم (ال)?الغاء|ألغيت|\b(is|has been|was) cancell?ed\b|"
                             r"\bI('ve| have) cancell?ed\b", re.IGNORECASE)),
    ("sent", re.compile(r"تم (ال)?إرسال (تفاصيل|الموقع|الموعد|التقرير)|أرسلت لك (تفاصيل|الموقع|الموعد|التقرير)|"
                        r"\bI('ve| have) sent you the (details|location|report)|\b(has been|was) sent to you\b",
                        re.IGNORECASE)),
]


def unbacked_claim(sentence: str, done: set[str], pending: str | None) -> str | None:
    """The claim a sentence makes ("booked", "sent" …) that no successful tool call backs (or whose action still
    awaits the caller's yes). The agent must never say it booked / confirmed / cancelled / sent something it didn't.
    `done`: claims backed by successful calls this call; `pending`: the claim of the parked action."""
    for claim, pattern in _CLAIMS:
        if pattern.search(sentence) and (claim == pending or claim not in done):
            return claim
    return None


# Second-person verbs the model leaves masculine when addressing a woman (Najdi): masculine → feminine.
_TO_FEMININE = {
    "تحس": "تحسين", "تبي": "تبين", "تبغى": "تبغين", "تحجز": "تحجزين", "تفضل": "تفضلين", "تقصد": "تقصدين",
    "تعاني": "تعانين", "تحتاج": "تحتاجين", "تختار": "تختارين", "تشوف": "تشوفين", "تقدر": "تقدرين",
    "تعرف": "تعرفين", "تحب": "تحبين", "تكون": "تكونين", "حاب": "حابة", "عطني": "عطيني", "قول": "قولي",
    "تروح": "تروحين", "تجي": "تجين", "تتصل": "تتصلين", "علمني": "علميني", "خبرني": "خبريني",
}
_MASC_WORD = re.compile(r"(?<![\w\u0600-\u06FF])([وف]?)(" + "|".join(sorted(_TO_FEMININE, key=len, reverse=True)) +
                        r")(?![\w\u0600-\u06FF])")


def to_feminine(text: str) -> str:
    """Address a female caller: rewrite the common masculine second-person verbs (تحس → تحسين)."""
    return _MASC_WORD.sub(lambda m: m.group(1) + _TO_FEMININE[m.group(2)], text)


# gpt-oss occasionally leaks its planning into the reply ("We need to list three nearest hospitals correctly.")
_REASONING = re.compile(
    r"^\W*(we|i) (need|should|must|have) to (list|respond|answer|say|mention|output|produce|follow|comply|reply|"
    r"format)\b|\b(call|use|invoke) (the|a) (\w+ )?tool\b|"
    r"\bthe (user|caller|assistant) (says|said|wants|asked|is asking|has)\b|"
    r"\baccording to (the )?(instructions|system|policy|guidelines)\b|\b(system|developer) (message|prompt)\b",
    re.IGNORECASE)


def is_reasoning_leak(sentence: str) -> bool:
    return bool(_REASONING.search(sentence)) or is_garbled(sentence)


# Real two-letter Arabic words — "وش حي أو مدينة تقيمين فيها؟" is a normal question, not a fragment soup.
_AR_SHORT_WORDS = set("وش في من لا أو او يا هل ما لك له لي هو هي إن ان أن عن مع حي ذا بس إذ اذ قد لم لن كم إي اي ثم".split())


def is_garbled(sentence: str) -> bool:
    """gpt-oss sometimes degenerates ("أقرب تو تو ت ت لل الت الت ك ك… حا"): mostly 1–2 letter fragments."""
    words = re.findall(r"[^\W\d_]+", sentence)
    if len(words) < 6:
        return False

    def fragment(w: str) -> bool:
        if "؀" <= w[0] <= "ۿ":
            return len(w) == 1 or (len(w) == 2 and w not in _AR_SHORT_WORDS)
        return len(w) == 1          # English is full of real 2-letter words ("is it ok by me")
    return sum(map(fragment, words)) / len(words) >= 0.4


_BRACKETED = re.compile(r"\[[^\]\n]{0,40}\]")


def clean_for_speech(text: str) -> str:
    text = re.sub("[​-‏⁠﻿]", "", text)     # zero-width / direction marks ("ب​مستشفى")
    text = _BRACKETED.sub(" ", _MD.sub(lambda m: m.group(1) or " ", text))
    text = re.sub(r"\s*\(([^()]*)\)", r" \1", text)          # "(غداً)" → "غداً": keep the words, not the brackets
    text = re.sub(r"\b(\w+)(?:\s+\1\b){2,}", r"\1", text)       # "Home Home Home" → "Home"
    text = _EMOJI.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


# ---------------- parsing helpers for HIS responses ----------------

_ID_KEYS = ("patientid", "patient_id", "patientno", "mrn", "id")
_DOB_KEYS = ("dateofbirth", "dob", "birthdate", "date_of_birth", "dateofbirthn")
_NAME_KEYS = ("firstname", "first_name", "firstnamen", "full_name_ar", "full_name_en", "fullname", "patientname",
              "patientnamen", "name", "namen", "first_name_ar", "first_name_en")


def patient_records(data: Any) -> list[dict[str, Any]]:
    """Normalize a patient lookup response into [{patient_id, first_name, names, dob}] (shape-tolerant)."""
    rows = _rows(data)
    out = []
    for r in rows:
        low = {k.lower(): v for k, v in r.items()}
        pid = next((low[k] for k in _ID_KEYS if low.get(k) not in (None, "")), None)
        if pid is None:
            continue
        names = [str(low[k]).strip() for k in _NAME_KEYS if low.get(k)]
        dob_raw = next((low[k] for k in _DOB_KEYS if low.get(k)), None)
        dob = resolve_dob(str(dob_raw)[:10]) if dob_raw else None
        gender = str(low.get("gender") or low.get("sex") or "").strip().lower()
        gender = "male" if gender in ("m", "male", "ذكر") else "female" if gender in ("f", "female", "انثى", "أنثى") \
            else None
        def first_of(keys):
            return next((str(low[k]).split()[0] for k in keys if low.get(k) and str(low[k]).split()), None)
        full_ar = next((str(low[k]).strip() for k in ("full_name_ar", "patientnamen", "namen") if low.get(k)), None)
        full_en = next((str(low[k]).strip() for k in ("full_name_en", "patientname", "name") if low.get(k)), None)
        first_ar = first_of(("firstnamen", "first_name_ar", "full_name_ar", "patientnamen", "namen"))
        first_en = first_of(("firstname", "first_name", "first_name_en", "full_name_en", "patientname", "name"))
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            pass
        out.append({"patient_id": pid, "first_name": first_ar or first_en, "first_name_ar": first_ar,
                    "first_name_en": first_en, "full_name_ar": full_ar, "full_name_en": full_en,
                    "gender": gender, "names": [normalize(n) for n in names],
                    "dob": dob.isoformat() if dob else None})
    return out


def otp_success(data: Any) -> bool:
    if isinstance(data, bool):
        return data
    if isinstance(data, str):
        t = data.lower()
        return any(w in t for w in ("verified", "success", "valid")) and not any(
            w in t for w in ("invalid", "incorrect", "expired", "fail", "not "))
    if isinstance(data, dict):
        low = {k.lower(): v for k, v in data.items()}
        for k in ("verified", "isverified", "isvalid", "valid", "success", "issuccess"):
            if k in low:
                return bool(low[k])
        status = str(low.get("status", "")).lower()
        if status:
            return status in ("success", "verified", "valid", "ok", "true")
        for v in low.values():
            if isinstance(v, (dict, str, bool)) and otp_success(v):
                return True
    return False


def _rows(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict):
        for v in data.values():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return v
        if any(k.lower() in _ID_KEYS for k in data):
            return [data]
        for v in data.values():
            if isinstance(v, dict):
                rows = _rows(v)
                if rows:
                    return rows
    return []


def _first_token_match(spoken: str, record_name: str) -> bool:
    """Spoken first name vs the record's first name — same script (fuzzy) or Arabic ↔ Latin (consonant skeleton)."""
    from rapidfuzz import fuzz
    spoken_first, first = spoken.split()[0], record_name.split()[0]
    if fuzz.ratio(spoken_first, first) >= 80:
        return True
    a, b = name_skeleton(spoken_first), name_skeleton(first)
    return bool(a and b) and (a == b or fuzz.ratio(a, b) >= 75 or (min(len(a), len(b)) >= 2 and (a in b or b in a)))


_AR_CONSONANTS = str.maketrans({
    "ب": "b", "ت": "t", "ث": "t", "ج": "j", "ح": "h", "خ": "k", "د": "d", "ذ": "d", "ر": "r", "ز": "z", "س": "s",
    "ش": "s", "ص": "s", "ض": "d", "ط": "t", "ظ": "z", "غ": "g", "ف": "f", "ق": "k", "ك": "k", "ل": "l", "م": "m",
    "ن": "n", "ه": "h", "ة": "", "ع": "", "ء": "", "ا": "", "أ": "", "إ": "", "آ": "", "ى": "", "و": "", "ي": "",
    "ئ": "", "ؤ": "",
})
_LATIN_MAP = [("kh", "k"), ("sh", "s"), ("th", "t"), ("dh", "d"), ("gh", "g"), ("ph", "f"), ("q", "k"), ("c", "k"),
              ("x", "ks"), ("v", "f"), ("p", "b")]


def name_skeleton(name: str) -> str:
    """Consonant skeleton of a first name, comparable across Arabic and Latin spellings:
    عبدالله / Abdullah → bdlh · محمد / Mohammed → mhmd · نورة / Noura → nr."""
    n = normalize(name).replace(" ", "")
    if any("؀" <= ch <= "ۿ" for ch in n):
        s = n.translate(_AR_CONSONANTS)
    else:
        s = n
        for a, b in _LATIN_MAP:
            s = s.replace(a, b)
        s = "".join(ch for ch in s if ch not in "aeiouwy'-")
        s = s[:-1] if s.endswith("h") and len(s) > 2 else s   # Noorah / Nourah ≈ نورة
    return re.sub(r"(.)\1+", r"\1", s)                    # Mohammed → mhmd


def _llm_view(args: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in args.items() if k not in ("PatientID", "PatientId", "patientId", "LanguageID", "languageId")}


def _j(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)
