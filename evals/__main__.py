"""Run conversation evals.

    python -m evals                          # all cases (LLM caller unless a case has a script)
    python -m evals --suite booking --judge  # one suite + LLM judge (naturalness / politeness / brevity / task)
    python -m evals --case book_by_name_ar --mode script
"""

import argparse
import asyncio
import sys

from .runner import load_cases, run_suite, save_report, store_run


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite")
    ap.add_argument("--case", action="append")
    ap.add_argument("--mode", default="auto", choices=["auto", "llm", "script"])
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument("--judge", action="store_true")
    ap.add_argument("--caller-model")
    a = ap.parse_args()
    cases = load_cases(a.suite, a.case)
    if not cases:
        raise SystemExit("no cases match")
    print(f"running {len(cases)} cases…\n")

    def progress(r):
        mark = "✓" if r["passed"] else "✗"
        failed = [c["check"] + (f" ({c['detail']})" if c["detail"] else "") for c in r["checks"] if not c["passed"]]
        print(f"{mark} {r['case']:<34} {r['turns']:>2} turns {r['duration_s']:>5}s  {r['mode']:<6} "
              f"{'; '.join(failed)[:150]}")

    async def run_and_store():
        run = await run_suite(cases, mode=a.mode, concurrency=a.concurrency, use_judge=a.judge,
                              caller_model=a.caller_model, progress=progress)
        try:
            await store_run(run)
        except Exception as e:
            print("(not stored in DB:", repr(e)[:80], ")")
        return run

    run = asyncio.run(run_and_store())
    s = run["summary"]
    print(f"\n{s['passed']}/{s['cases']} cases passed · LLM first token p50 {s['llm_first_token_p50']} ms · "
          f"{s.get('words_per_reply')} words / reply (longest {s.get('longest_reply_words')})")
    for k, v in s["checks"].items():
        print(f"  {k:<24} {v['passed']}/{v['total']}")
    if s["judge"]:
        print("  judge:", s["judge"])
    print("report:", save_report(run))


if __name__ == "__main__":
    main()
