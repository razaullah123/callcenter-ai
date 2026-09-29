"""Accuracy of the LLM extraction on garbled speech-to-text transcripts (real call patterns + known STT errors).

    python scripts/extract_bench.py [--model openai/gpt-oss-120b] [--runs 2]

Each case says what the correct answer is — including None where nothing may be extracted (a digit missing,
no code said): inventing a value there is the worst failure.
"""

import argparse
import asyncio
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from runtime.harness.nlu.dates import today_riyadh  # noqa: E402
from runtime.harness.nlu.extract import extract  # noqa: E402
from runtime.harness.nlu.numbers import extract_code, normalize_mobile  # noqa: E402
from runtime.providers import create  # noqa: E402

TODAY = date(2026, 9, 28)          # Monday — fixed so relative dates have one right answer
OFFERED = {"08:00", "08:15", "08:30", "14:30", "14:45", "15:00", "10:00", "10:10", "10:20"}

CASES = [
    # --- mobile
    ("mobile", "الرقم هو خمسمية وأربعين ... صفر خمسة أربعة ثمانية ثمانية صفر تسعة وعشرين ثمانية وستين", "0548802968"),
    ("mobile", "رقمي صفر خمسة خمسة، واحد اثنين ثلاثة، أربعة خمسة ستة سبعة", "0551234567"),
    ("mobile", "it's oh five five, one two three, four five six seven", "0551234567"),
    ("mobile", "zero five four eight eight, sorry, zero five four eight eight zero two nine six eight", "0548802968"),
    ("mobile", "جوالي ٠٥٤٨٨٠٢٩٦٨", "0548802968"),
    ("mobile", "الرقم تسعمية ستة وستين خمسة أربعة ثمانية ثمانية صفر اثنين تسعة ستة ثمانية", "0548802968"),
    ("mobile", "صفر خمسة أربعة ثمانية ثمانية صفر اثنين تسعة ستة", None),             # 9 digits: must not invent
    ("mobile", "That's ••••68.", None),                                                 # masked / incomplete
    ("mobile", "ابي احجز موعد", None),
    # --- otp
    ("otp", "الرمز أربعة ستة سبعة، اه، ثلاثة واحد خمسة", "467315"),
    ("otp", "the code is four six seven... three one five", "467315"),
    ("otp", "ثمنطعش واحد وتسعين خمسطعش", "189115"),
    ("otp", "It's 8 6 6 0 8 9", "866089"),
    ("otp", "ما وصلني الرمز", None),
    # --- date (today is Monday 2026-09-28)
    ("date", "أبي يوم الأحد الجاي", "2026-10-04"),
    ("date", "خمسطعش نوفمبر", "2026-11-15"),
    ("date", "I would like to book for fifteenth of November", "2026-11-15"),
    ("date", "بعد بكرة", "2026-09-30"),
    ("date", "أبي أغري في موعد مطاح", "earliest"),
    ("date", "any day is fine, the soonest", "earliest"),
    ("date", "next tuesday please", "2026-09-29"),
    # --- time choice
    ("time_choice", "الساعة ثنتين ونص", "14:30"),
    ("time_choice", "the first one", "14:30"),
    ("time_choice", "ثلاث العصر", "15:00"),
    ("time_choice", "quarter to three", "14:45"),
    ("time_choice", "أبي الساعة خمس", None),                                             # not offered
    # --- date of birth + name
    ("dob_name", "خمسطعش مارس ألف وتسعمية وتسعين، اسمي محمد", ("1990-03-15", "محمد")),
    ("dob_name", "my name is Noura, born first of July ninety four", ("1994-07-01", "Noura")),
]
# what the engine passes: the agent's last line (the question being answered)
CONTEXT = {"time_choice": "The agent had just said: The available time slots with Dr. Rubeena Quadri are Tuesday at "
                          "2:30 PM, 2:45 PM and 3:00 PM. Which time slot suits you?",
           "date": "The agent had just said: تبي أقرب موعد متاح، ولا عندك تاريخ معين؟",
           "mobile": "The agent had just said: May I have your registered mobile number, please?",
           "otp": "The agent had just said: Could you please tell me the code?",
           "dob_name": "The agent had just said: لقيت أكثر من ملف على هالرقم، ممكن تاريخ الميلاد والاسم الأول؟"}


def norm(field, value):
    if value is None:
        return None
    if field == "date":
        return value if value == "earliest" else value.isoformat()
    if field == "dob_name":
        return (value["date_of_birth"].isoformat() if value["date_of_birth"] else None, value["first_name"])
    return value


def regex_value(field, text):
    return {"mobile": normalize_mobile, "otp": extract_code}.get(field, lambda _t: None)(text)


async def main(model: str, runs: int) -> None:
    llm = create("llm", "groq", {"model": model})
    ok = total = invented = 0
    latencies = []
    for field, text, want in CASES:
        results = []
        for _ in range(runs):
            t0 = time.perf_counter()
            r = await extract(llm, field, text, today=TODAY, offered=OFFERED if field == "time_choice" else None,
                              context=CONTEXT.get(field, ""))
            latencies.append((time.perf_counter() - t0) * 1000)
            got = norm(field, r.value)
            if field == "dob_name" and got:
                good = got[0] == want[0] and (got[1] or "").lower() == want[1].lower()
            else:
                good = got == want
            results.append(good)
            invented += want is None and got is not None
        ok += sum(results)
        total += len(results)
        rx = regex_value(field, text)
        print(f"{'✓' if all(results) else '✗'} {field:<11} want={str(want):<22} llm={str(got):<22} regex={str(rx):<12} {text[:60]}")
    latencies.sort()
    print(f"\n{model}: {ok}/{total} correct · invented a value where none was said: {invented} · "
          f"latency p50 {latencies[len(latencies) // 2]:.0f} ms, p90 {latencies[int(len(latencies) * .9)]:.0f} ms")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="openai/gpt-oss-120b")
    ap.add_argument("--runs", type=int, default=2)
    a = ap.parse_args()
    asyncio.run(main(a.model, a.runs))
