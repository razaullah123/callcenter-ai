"""Fast rule-based checks run on every caller utterance (no LLM round trip)."""

import re

# "headache since today / for a week" is not a requested appointment day
_SINCE = re.compile(r"(^|\s)(من|منذ|له|لها)\s+(ال)?(يوم|امس|أمس|البارحه|البارحة|اسبوع|أسبوع|شهر|يومين)|\bsince\b|\bfor (a|one|two|\d+) "
                    r"(day|days|week|weeks|month|months)\b", re.IGNORECASE)
# "the earliest appointment" — not "أقرب مستشفى" (the nearest hospital)
_EARLIEST = re.compile(r"[أا]قرب (موعد|وقت|يوم)|[أا]ول (موعد|وقت|يوم)|[أا]ي (يوم|وقت)|ب[أا]سرع وقت|^\W*(ال)?[أا]قرب\W*$|"
                       r"earliest|soonest|first available|as soon as possible|\basap\b|any (day|time)|"
                       r"nearest (appointment|date|slot|time)", re.IGNORECASE)


def mentions_since(text: str) -> bool:
    return bool(_SINCE.search(text))


# "I'm at / near Olaya" — the caller is saying where they are (a location search), not naming a hospital
_WHERE_I_AM = re.compile(
    # "I'm (located) at / in / near X", "I live in X", "my location", "near X" — not a bare "I'm …"
    r"\b(i'?m|i am|we'?re|currently|located|staying|living|i live|we live)\b(\s+\w+){0,3}?\s+"
    r"(at|in|near|nearby|around|close to|next to)\b|\bmy location\b|\b(near|nearby|close to|around)\s+(?!.*hospital)|"
    r"(^|\s)([أا]نا (في|ب|بـ|عند|قريب|جنب|ساكن)|ساكن|ساكنه|موقعي|قريب من|قريبه من|جنب|حولي|بالقرب)", re.IGNORECASE)


def describes_location(text: str) -> bool:
    return bool(_WHERE_I_AM.search(text))


def wants_earliest(text: str) -> bool:
    return bool(_EARLIEST.search(text))


# Right after "the earliest available, or a specific date?", a loose answer means "the earliest".
_ANY = re.compile(r"(^|\s)([أا]ول|[أا]ي|اللي ?بعد|ما يفرق|ما تفرق|مو مهم|مايهم|ما يهم|عادي|نعم|ايه|إيه|اي نعم|تمام|"
                  r"متاح|مطاح|متاحة|مطاحة)(\s|\W|$)|"
                  r"\b(first|any|whatever|doesn'?t matter|no preference|yes|yeah|sure|fine|ok(ay)?)\b", re.IGNORECASE)


def loose_earliest(text: str) -> bool:
    return bool(_ANY.search(text))

from runtime.text.arabic import normalize

_YES = set("""
ايه ايوه اي ايوا ايواا نعم اكيد تمام طيب صح صحيح يب يس اوكي اوك ماشي زين موافق اكد احجز احجزه اعتمد اعتمده
بالضبط مضبوط
yes yeah yep yup sure ok okay correct right confirm confirmed exactly please
""".split())
_NO = set("""
لا لاء مو مب ماابي ماابغي ماابغا لالا غلط خطا الغ الغي كنسل
no nope nah wrong not cancel dont don't
""".split())
_NO_PHRASES = re.compile(r"\b(ما ابي|ما ابغي|ما ابغا|مو هذا|مو صح|لا شكرا|no thanks|not that|don'?t)\b")


def yes_no(text: str) -> str | None:
    """'yes' / 'no' for a reply to a yes/no question, None if unclear. Negation wins."""
    t = normalize(text)
    if not t:
        return None
    if _NO_PHRASES.search(t):
        return "no"
    tokens = t.split()
    if any(tok in ("بس", "لكن", "بشرط", "but", "except") for tok in tokens):
        return None  # "طيب بس ابي وقت ثاني" — a condition / objection, not a clean yes
    head = tokens[:4]  # the answer is at the start of a short reply
    if any(tok in _NO for tok in head):
        return "no"
    if any(tok in _YES for tok in head) or " ".join(head[:3]) in _YES:
        return "yes"
    return None


_GREETING_WORDS = set("""
السلام عليكم وعليكم سلام مرحبا مرحبتين هلا هلا والله اهلا اهلين يا هلا صباح مساء الخير النور كيفك كيف حالك
الحال شلونك شخبارك عساك بخير الحمدلله الله يسلمك يعطيك العافيه يا اخوي يا اخي يا اخت يا استاذ
hi hello hey hiya good morning afternoon evening day there how are you doing salam salaam alaikum
assalamu alaykum
""".split())


def greeting_only(text: str) -> bool:
    """True when the caller only greeted ("hi", "السلام عليكم", "مساء الخير كيف الحال") without a request."""
    tokens = [t for t in normalize(text).split() if t not in ("و", "يا", "ال")]
    stripped = [t[1:] if t.startswith("و") and t[1:] in _GREETING_WORDS else t for t in tokens]
    return bool(stripped) and all(t in _GREETING_WORDS for t in stripped)


# Greeting back in kind — matched to what the caller actually said (the model copied "وعليكم السلام" to a "مرحبا").
_AR_GREETS = [(re.compile(r"السلام|سلام عليكم"), "وعليكم السلام ورحمة الله."),
              (re.compile(r"صباح"), "صباح النور."),
              (re.compile(r"مساء"), "مساء النور."),
              (re.compile(r"مرحب|هلا|اهلا|اهلين"), "هلا والله، مرحبتين.")]
_AR_HOW = re.compile(r"كيف ?حال|كيفك|كيف الحال|شلون|شخبار|عساك بخير")
_EN_GREETS = [(re.compile(r"good morning"), "Good morning!"), (re.compile(r"good afternoon"), "Good afternoon!"),
              (re.compile(r"good evening"), "Good evening!"), (re.compile(r"salam|assalam"), "Wa alaikum assalam!"),
              (re.compile(r"(hi|hello|hey|hiya)"), "Hello!")]
_EN_HOW = re.compile(r"how are you|how r u|how('s| is) it going|how do you do")
GREETING_FOLLOW_UP = {"ar": "كيف أقدر أخدمك اليوم؟", "en": "How can I help you today?"}


def greeting_reply(text: str, language: str) -> str:
    """The caller only greeted: greet back in kind, then ask how to help."""
    t = normalize(text)
    if language == "ar":
        greet = next((reply for pattern, reply in _AR_GREETS if pattern.search(t)), "هلا والله.")
        how = " الحمدلله بخير، الله يسلمك." if _AR_HOW.search(t) else ""
    else:
        greet = next((reply for pattern, reply in _EN_GREETS if pattern.search(t)), "Hello!")
        how = " I'm fine, thank you." if _EN_HOW.search(t) else ""
    return f"{greet}{how} {GREETING_FOLLOW_UP[language]}"


_HUMAN = re.compile(
    r"(موظف|انسان|شخص حقيقي|احد يكلمني|خدمه العملاء|اكلم احد|حولني|تحويل|مشرف|"
    r"\bagent\b|\bhuman\b|representative|real person|operator|customer service|transfer me)")


def wants_human(text: str) -> bool:
    return bool(_HUMAN.search(normalize(text)))


# Symptoms that must be sent to emergency care, never to a clinic booking.
_RED_FLAGS = re.compile("|".join([
    r"(الم|وجع|كتمه|ضغط) (في |ب)?(ال)?صدر", r"صدري (يوجعني|يعورني|ضايق|مكتوم)",
    r"(ضيق|صعوبه) (في |ب)?(ال)?(تنفس|نفس)", r"ما (اقدر|قادر) (اتنفس|اتنفس)", r"اختناق",
    r"نزيف (قوي|شديد|ما وقف)", r"(اغمي|اغماء|فقد(ت)? (ال)?وعي|طاح وما صحي)",
    r"(جلطه|سكته|شلل مفاجئ|وجهي (مايل|مايله)|ما اقدر احرك)",
    r"(تشنج|نوبه صرع)", r"(انتحار|ابي اموت|اذي نفسي|اقتل نفسي)", r"\b(تسمم|بلعت? (دوا|علاج) كثير)",
    r"chest pain", r"(can'? ?t|cannot) breathe", r"shortness of breath", r"heavy bleeding",
    r"(stroke|unconscious|seizure|suicid|overdose|poison)",
]))


def red_flag(text: str) -> bool:
    return bool(_RED_FLAGS.search(normalize(text)))


# Which clinic a red-flag symptom belongs to (the model took "chest pain" to the chest / pulmonary clinic).
_RED_FLAG_CLINICS = [
    (re.compile(r"(الم|وجع|كتمه|ضغط) (في |ب)?(ال)?صدر|صدري (يوجعني|يعورني|مكتوم)|chest pain|palpitation|خفقان"),
     "cardiology (القلب), not the chest / pulmonary clinic (الصدرية)"),
    (re.compile(r"(ضيق|صعوبه) (في |ب)?(ال)?(تنفس|نفس)|ما (اقدر|قادر) اتنفس|اختناق|can'? ?t breathe|cannot breathe|"
                r"shortness of breath"), "pulmonary / chest medicine (الصدرية)"),
    (re.compile(r"جلطه|سكته|شلل|وجهي (مايل|مايله)|ما اقدر احرك|تشنج|صرع|stroke|seizure"),
     "neurology (المخ والأعصاب)"),
]


def red_flag_clinic(text: str) -> str | None:
    t = normalize(text)
    return next((clinic for pattern, clinic in _RED_FLAG_CLINICS if pattern.search(t)), None)


EMERGENCY_MESSAGE = {
    "ar": "سلامتك. الأعراض اللي تذكرها ممكن تكون طارئة، الله يحفظك توجّه لأقرب طوارئ فوراً أو اتصل على ٩٩٧. "
          "إذا تحتاج شي ثاني أنا موجود.",
    "en": "I'm sorry you're feeling this way. These symptoms may be an emergency — please go to the nearest "
          "emergency department now or call 997. I'm here if you need anything else.",
}
