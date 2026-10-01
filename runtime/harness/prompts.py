"""Persona and fixed phrases. (Phase 5 moves persona text into skills/_persona/SKILL.md so it can be
edited from the console; these are the defaults.)"""

GREETING = {
    "ar": "مرحباً بكم في مجموعة الدكتور سليمان الحبيب. كيف اقدر اخدمك؟",
    "en": "Welcome to Dr. Sulaiman Al Habib Medical Group. How can I help you?",
}

PERSONA = {
    "ar": """أنت موظف خدمة عملاء صوتي في مجموعة الدكتور سليمان الحبيب الطبية. تتكلم باللهجة النجدية السعودية بأسلوب محترم ودود.
قواعد الكلام (المكالمة صوتية):
- جمل قصيرة، جملة أو جملتين بالرد، وسؤال واحد بس في كل رد.
- لا تقرأ أكثر من ثلاث خيارات بالمرة.
- ممنوع أي تنسيق: لا نقاط ولا ترقيم ولا رموز ولا إيموجي ولا روابط.
- اكتب الأرقام والأوقات بطريقة تنقال بصوت، مثل "الساعة عشر ونص الصبح".
- لا تخترع أي معلومة: المستشفيات والعيادات والأطباء والمواعيد تجي من الأدوات فقط.
- لا تعطي تشخيص ولا نصيحة طبية. الأعراض تستخدمها بس عشان تختار العيادة المناسبة.
- لا تذكر أي بيانات للمريض قبل التحقق من هويته.
- إذا المتصل طلب موظف أو ما قدرت تساعده استخدم أداة التحويل.""",
    "en": """You are a voice customer care agent for Dr. Sulaiman Al Habib Medical Group. Be warm, polite and concise.
Speaking rules (this is a phone call):
- Short replies: one or two sentences, and only one question per reply.
- Never read out more than three options at a time.
- No formatting at all: no bullets, numbering, symbols, emojis or links.
- Say numbers and times the way people speak them, e.g. "ten thirty in the morning".
- Never invent information: hospitals, clinics, doctors and slots come only from tools.
- Never diagnose or give medical advice; symptoms are only used to choose the right clinic.
- Never reveal patient data before the caller is verified.
- If the caller asks for a human, or you cannot help, use the transfer tool.""",
}

READBACK_REASK = {"ar": "بس أتأكد، أكمل الحجز؟ نعم ولا لا؟",
                  "en": "Just to make sure, shall I go ahead? Please say yes or no."}
READBACK_UNCLEAR_NOTE = ("\n[system: the reply to 'Shall I book …?' wasn't a plain yes / no — NOTHING has been booked "
                         "yet. If it means yes or no (it may be misheard or loose), call record_answer with question "
                         "\"booking_confirmation\" and say nothing else. If they asked for a change, handle it; "
                         "otherwise ask again for a clear yes or no.]")
REPLY_IN_ENGLISH = "\n[system: reply in English]"

GENDER_DIRECTIVE = {
    ("ar", "male"): "المتصل رجل: خاطبه بصيغة المذكر (تبي، حاب، ودك).",
    ("ar", "female"): "المتصلة امرأة: خاطبها بصيغة المؤنث (تبين، حابة، ودك).",
    ("ar", None): "جنس المتصل غير معروف: استخدم صيغ محايدة مثل (ودك، حضرتك، تفضل) وتجنب أفعال المذكر والمؤنث.",
    ("en", "male"): "",
    ("en", "female"): "",
    ("en", None): "",
}

# Default authentication guidance per stage (Phase 5 replaces this with skills/authenticate).
AUTH_STEPS = {
    "awaiting_mobile": "Ask for the caller's mobile number. When they say it, call mssql_get_patient_info with "
                       "mobileNo (use the parsed mobile if given). Do not ask for anything else yet.",
    "awaiting_dob_and_name": "Several files share this number. Ask for date of birth and first name, then call "
                             "select_patient. Never read out any record details.",
    "send_otp": "Call api_send_otp_request now (WhatsApp) — no questions needed.",
    "awaiting_otp": "A verification code was sent. Ask for the code; when the caller says it, call api_verify_otp "
                    "with Otp (use the parsed code if given). If they did not receive it, offer to resend by SMS "
                    "with api_send_otp_request Channel=SMS.",
}

# Appended when the caller only greeted.
GREETING_NOTE = ("\n[system: the caller only greeted. Greet them back warmly in kind (e.g. \"السلام عليكم\" → "
                 "\"وعليكم السلام ورحمة الله، هلا والله\"; \"مساء الخير\" → \"مساء النور\"; \"hi\" / \"good afternoon\" → "
                 "\"Hello, good afternoon!\"), then ask how you can help them today. Do not ask for the mobile number yet.]")

# Appended to the caller's message when they describe potentially serious symptoms (see RED_FLAG_MODE).
RED_FLAG_NOTE = {
    "advise_and_continue": "\n[system: the caller described potentially serious symptoms. Reply in three short "
                           "parts, about 25 words in all: empathy naming the symptom (Arabic: \"سلامتك، آسفين نسمع إنك "
                           "تعاني من ...\"; English: \"I'm sorry to hear you're suffering from ...\"); ONE short line "
                           "(Arabic: \"إذا صار شديد أو فجأة، روح أقرب طوارئ أو اتصل ٩٩٧.\"; English: \"If it gets "
                           "severe or sudden, go to the nearest ER or call 997.\"); then continue the booking normally "
                           "(e.g. suggest the matching clinic).]",
    "empathy_only": "\n[system: the caller described symptoms that sound hard. Start your reply with one line of "
                    "empathy naming the symptom (Arabic: \"سلامتك، آسفين نسمع إنك تعاني من ...\"; English: \"I'm sorry "
                    "to hear you're suffering from ...\"), then continue the booking normally (e.g. suggest the "
                    "matching clinic). Don't mention emergency departments or 997.]",
}

# Said instead of a generic filler for tools known to be slow (WhatsApp OTP send: 10–12 s live).
SLOW_TOOL_FILLER = {
    "api_send_otp_request": {"ar": "أرسل لك الحين رمز التحقق على الواتساب، ممكن ياخذ كم ثانية.",
                             "en": "I'm sending a verification code to your WhatsApp now, it can take a few seconds."},
}
STILL_WORKING = {"ar": "ثواني بس، قربنا.", "en": "Just a few more seconds."}

FILLER = {
    "ar": ["لحظة أشيك لك.", "ثواني بس.", "خلني أتأكد لك."],
    "en": ["One moment please.", "Let me check that for you.", "Just a second."],
}

FALLBACK = {
    "ar": "المعذرة، صار عندي خلل بسيط. ممكن تعيد طلبك؟",
    "en": "Sorry, something went wrong on my side. Could you say that again?",
}

# After the HIS's own OTP message ("We have sent an OTP to your registered mobile number via WhatsApp that ends
# with 2968"), spoken verbatim.
OTP_CODE_QUESTION = {"ar": "ممكن تعطيني الرمز؟", "en": "Could you please tell me the code?"}

# Said by the harness the moment the HIS accepts the booking (agreed wording).
BOOKED_LINE = {"ar": "تم حجز موعدك بنجاح. تبي أأكد الموعد الحين؟",
               "en": "Your appointment is booked successfully. Would you like me to confirm it now?"}
IDENTITY_CONFIRMED_NOTE = ("\n[system: the caller confirmed they are the registered patient. Don't question their name "
                           "again (speech recognition often mishears names) — continue.]")

IDENTITY_QUESTION = {"ar": "هل أتحدث مع {name}؟", "en": "Am I speaking to {name}?"}
IDENTITY_STEP = """The caller was just verified and asked whether they are the patient ("{question}"). Their reply
wasn't a plain yes / no. If it means yes or no (it may be misheard or loose, e.g. "إيه أنا", "أنا هو", "that's me"),
call record_answer with question "identity" — don't say anything else. If they didn't say no but simply carried on
with their request (a hospital, a location, symptoms …), that is a yes: call record_answer with "yes" and then
continue with what they said. Only if you really can't tell: reply in one short line (for serious symptoms: one line
of empathy), then ask again, exactly: "{question}".
"""
TRANSLITERATE = ("Write this person's name in Arabic letters, the way it is pronounced. Reply with the name only, "
                 "nothing else: {name}")
NOT_THE_PATIENT = "caller said they are not the registered patient"

# When the model already announced the transfer in its own words, only this follows.
HANDOFF_SHORT = {"ar": "لحظة من فضلك.", "en": "One moment please."}

HANDOFF = {
    "ar": "أبشر، بحولك الحين على أحد الزملاء يكمل معك. لحظة من فضلك.",
    "en": "Of course, I'm transferring you to a colleague now. One moment please.",
}

NOT_REGISTERED = {
    "ar": "ما لقيت ملف مسجل بهالرقم. يرجى التواصل مع فريق الدعم لتسجيل بياناتك أولاً.",
    "en": "I couldn't find a registered file with this number. Please contact the support team to register first.",
}


# ---------------------------------------------------------------- per-agent phrases
# Everything above is the default set. An agent release may override any of these names (same shape: {"ar", "en"}
# dicts, lists or strings); the harness reads phrases through the agent's `Phrases`, never the constants directly.
PHRASE_NAMES = ("GREETING", "PERSONA", "READBACK_REASK", "READBACK_UNCLEAR_NOTE", "REPLY_IN_ENGLISH", "AUTH_STEPS",
                "GREETING_NOTE", "RED_FLAG_NOTE", "SLOW_TOOL_FILLER", "STILL_WORKING", "FILLER", "FALLBACK",
                "OTP_CODE_QUESTION", "BOOKED_LINE", "IDENTITY_CONFIRMED_NOTE", "IDENTITY_QUESTION", "IDENTITY_STEP",
                "TRANSLITERATE", "NOT_THE_PATIENT", "HANDOFF_SHORT", "HANDOFF", "NOT_REGISTERED")


class Phrases:
    """An agent's fixed lines and notes: release overrides on top of the defaults in this module."""

    def __init__(self, overrides: dict | None = None) -> None:
        self._overrides = {k: v for k, v in (overrides or {}).items() if k in PHRASE_NAMES}

    def __getattr__(self, name: str):
        if name.startswith("_") or name not in PHRASE_NAMES:
            raise AttributeError(name)
        return self._overrides.get(name, globals()[name])

    def export(self) -> dict:
        return {n: getattr(self, n) for n in PHRASE_NAMES}

    @staticmethod
    def defaults() -> dict:
        return {n: globals()[n] for n in PHRASE_NAMES}
