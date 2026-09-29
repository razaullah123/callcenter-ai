from datetime import date

import pytest

from runtime.harness.nlu.dates import resolve_date, resolve_dob
from runtime.harness.nlu.gender import GenderResolver, gender_from_name, gender_from_text
from runtime.harness.nlu.intents import red_flag, wants_human, yes_no
from runtime.harness.nlu.lang import LanguageTracker
from runtime.harness.nlu.numbers import extract_code, normalize_mobile, spoken_number

SUNDAY = date(2026, 9, 27)


@pytest.mark.parametrize("text", [
    "0541234567", "٠٥٤١٢٣٤٥٦٧", "+966 54 123 4567", "966541234567", "541234567",
    "صفر خمسة أربعة واحد اثنين ثلاثة أربعة خمسة ستة سبعة",
    "خمسه اربعه واحد اثنين تلاته اربعه خمسه سته سبعه",
    "zero five four one two three four five six seven",
])
def test_mobile(text):
    assert normalize_mobile(text) == "0541234567"


def test_mobile_compound_and_incomplete():
    assert normalize_mobile("صفر خمسه اربعه خمسطعش ثلاث وخمسين اثنين ثمانيه سبعه") == "0541553287"
    assert normalize_mobile("zero five four double one two three four five six") == "0541123456"
    assert normalize_mobile("رقمي ٠٥٥") is None


@pytest.mark.parametrize("text,code", [("واحد اثنين ثلاثه اربعه", "1234"), ("الكود 8 4 2 1 9 0", "842190"),
                                       ("الرمز ثلاث وعشرين سبعه ثمانيه", "2378"), ("ما وصلني", None)])
def test_otp(text, code):
    assert extract_code(text) == code


@pytest.mark.parametrize("text,expected", [
    ("بكرة", "2026-09-28"), ("بعد بكره", "2026-09-29"), ("اليوم", "2026-09-27"), ("الخميس", "2026-10-01"),
    ("الأحد الجاي", "2026-10-04"), ("بعد يومين", "2026-09-29"), ("بعد ثلاث ايام", "2026-09-30"),
    ("بعد اسبوع", "2026-10-04"), ("خمسطعش أكتوبر", "2026-10-15"), ("١٥ اكتوبر", "2026-10-15"),
    ("3 سبتمبر", "2027-09-03"), ("2026-10-05", "2026-10-05"), ("٢٠٢٦/١٠/٠٥", "2026-10-05"),
    ("next monday", "2026-09-28"), ("October 20", "2026-10-20"), ("ابي اقرب موعد", None),
])
def test_dates(text, expected):
    d = resolve_date(text, SUNDAY)
    assert (d.isoformat() if d else None) == expected


@pytest.mark.parametrize("text", ["15 مارس 1990", "خمسطعش مارس الف وتسعمية وتسعين", "1990-03-15", "15/03/1990",
                                  "March 15 nineteen ninety"])
def test_dob(text):
    assert resolve_dob(text) == date(1990, 3, 15)


def test_dob_rejects_relative():
    assert resolve_dob("بكرة") is None


@pytest.mark.parametrize("text,n", [("الف وتسعمية وتسعين", 1990), ("الفين وخمسة", 2005), ("two thousand ten", 2010),
                                    ("nineteen ninety", 1990), ("الفين واربعطعش", 2014)])
def test_years(text, n):
    assert spoken_number(text) == n


@pytest.mark.parametrize("text,answer", [
    ("ايه", "yes"), ("ايوه صح", "yes"), ("ايه احجزه", "yes"), ("تمام", "yes"), ("yes please", "yes"),
    ("لا", "no"), ("لا مو هذا", "no"), ("ما ابي", "no"), ("no thanks", "no"),
    ("طيب بس ابي وقت ثاني", None), ("الخميس", None),
])
def test_yes_no(text, answer):
    assert yes_no(text) == answer


@pytest.mark.parametrize("text,gender", [("أنا تعبانة من أمس", "female"), ("انا تعبان", "male"), ("أنا حامل", "female"),
                                         ("زوجي مريض", "female"), ("زوجتي تبي موعد", "male"), ("ابي موعد", None)])
def test_gender_from_text(text, gender):
    r = gender_from_text(text)
    assert (r[0] if r else None) == gender


def test_gender_resolver_prefers_stronger_evidence():
    g = GenderResolver()
    assert not g.observe(gender_from_name("محمد"), "patient_name")   # 0.6 < threshold → still neutral
    assert g.known is None
    assert g.observe(gender_from_text("انا تعبانه"), "speech")
    assert g.known == "female"
    g.observe(gender_from_name("محمد"), "patient_name")               # weaker evidence doesn't override
    assert g.known == "female"


@pytest.mark.parametrize("text,flag", [("عندي ألم في صدري", True), ("صدري يوجعني", True), ("ما اقدر اتنفس", True),
                                       ("I have chest pain", True), ("اسمي محمد", False),
                                       ("عندي حبوب في وجهي", False)])
def test_red_flags(text, flag):
    assert red_flag(text) is flag


@pytest.mark.parametrize("text,human", [("ابي اكلم موظف", True), ("حولني على خدمة العملاء", True),
                                        ("I want a human", True), ("ابي احجز", False)])
def test_wants_human(text, human):
    assert wants_human(text) is human


def test_language_tracker():
    lt = LanguageTracker()
    lt.update("I want to book an appointment")
    assert lt.language == "en" and lt.language_id == 2
    lt.update("مرحبا")
    assert lt.language == "en"            # one Arabic word doesn't flip the call
    lt.update("تمام")
    assert lt.language == "ar"            # two consecutive turns do
    lt.update("can you speak english please")
    assert lt.language == "en"            # explicit request switches immediately


@pytest.mark.parametrize("text,greet", [
    ("hi", True), ("hi good afternoon", True), ("السلام عليكم", True), ("مساء الخير", True),
    ("هلا والله كيف الحال", True), ("hi I want to book", False), ("السلام عليكم ابي احجز موعد", False),
    ("0548802968", False), ("ايه", False), ("طيب", False), ("thanks", False),
])
def test_greeting_only(text, greet):
    from runtime.harness.nlu.intents import greeting_only
    assert greeting_only(text) is greet


def test_red_flag_clinic_routing():
    from runtime.harness.nlu.intents import red_flag, red_flag_clinic
    assert "القلب" in red_flag_clinic("عندي ألم في صدري من أسبوع")
    assert "cardiology" in red_flag_clinic("I have chest pain")
    assert red_flag("I can't breathe well") and "pulmonary" in red_flag_clinic("I can't breathe well")
    assert "neurology" in red_flag_clinic("وجهي مايل فجأة")
