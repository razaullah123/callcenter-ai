"""Text chat with the agent harness (no audio) — real Groq LLM.

    python scripts/chat.py                       # interactive
    python scripts/chat.py --script flows.txt    # one caller line per row
    python scripts/chat.py --offline             # all tools mocked (no HIS access)
    python scripts/chat.py --live-auth           # use YOUR real mobile: real patient lookup + real OTP
    python scripts/chat.py --live-auth --live-booking   # + REAL booking / confirmation in the HIS

Safety: by default patient / authentication / write tools are mocked with fake data (test mobile
0500000001, OTP 1234, fake bookings). Only non-patient booking lookups (hospitals near a location,
clinics, doctor slots) go to the live HIS unless --offline. --live-auth additionally sends the three
authentication tools to the live HIS (a real OTP is sent to the caller's WhatsApp); their responses
are shown as field names / types only. Write tools (booking, cancel, complaints, sends) stay mocked.
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")
sys.stdin.reconfigure(encoding="utf-8")

from runtime.config import get_settings  # noqa: E402
from runtime.events import ConsoleSink, EventBus, JsonlSink, Level  # noqa: E402
from runtime.harness.engine import Agent  # noqa: E402
from runtime.harness.redact import redact_event  # noqa: E402
from runtime.harness.session import Session  # noqa: E402
from runtime.skills import FileSkillSet  # noqa: E402
from runtime.providers import create  # noqa: E402
from runtime.tools import MCPPool, MockMCP  # noqa: E402
from runtime.tools.factory import build_tooling  # noqa: E402

LIVE_SAFE = {
    "mssql_get_Projects_from_Location", "mssql_get_clinics_for_project",
    "mssql_get_TopFive_nearestClinic_have_doctorSlots", "mssql_get_nearestClinic_have_doctorSlots",
    "mssql_get_TopFive_availableDoctors_with_slots_byDate", "mssql_get_availableDoctors_with_slots_byDate",
}


def fake_backend() -> MockMCP:
    mock = MockMCP()
    mock.on("mssql_get_patient_info", lambda a: [{"PatientID": 900001, "FirstNameN": "محمد", "FirstName": "Mohammed",
                                                  "DateofBirth": "1990-03-15"}]
            if a.get("mobileNo") == "0500000001" else [])
    mock.on("api_send_otp_request", {"success": True})
    mock.on("api_verify_otp", lambda a: {"success": str(a.get("Otp")) == "1234"})
    mock.on("api_book_Appointment", lambda a: {"success": True, "AppointmentNo": 7700001})
    mock.on("mssql_confirm_appointment", {"success": True})
    mock.on("api_send_AppointmentWhatsapp", {"success": True})
    mock.on("api_send_AppointmentSms", {"success": True})
    return mock


AUTH_TOOLS = {"mssql_get_patient_info", "api_send_otp_request", "api_verify_otp"}
BOOKING_TOOLS = {"api_book_Appointment", "mssql_confirm_appointment"}


def shape(value, depth=0):
    """Field names and types only — never values (safe to print for patient data)."""
    if isinstance(value, dict):
        return {k: shape(v, depth + 1) for k, v in value.items()} if depth < 4 else "{…}"
    if isinstance(value, list):
        return [shape(value[0], depth + 1), f"×{len(value)}"] if value else []
    return type(value).__name__


class HybridMCP:
    """Live HIS for LIVE_SAFE tools (and AUTH_TOOLS with --live-auth), fake data for everything else."""

    def __init__(self, live: MCPPool | None, fake: MockMCP, live_auth: bool = False,
                 live_booking: bool = False) -> None:
        self.live, self.fake = live, fake
        self.live_tools = LIVE_SAFE | (AUTH_TOOLS if live_auth else set()) | (BOOKING_TOOLS if live_booking else set())

    async def start(self):
        if self.live:
            await self.live.start()

    async def close(self):
        if self.live:
            await self.live.close()

    def schemas(self):
        return self.fake.schemas()

    async def call(self, name, args, timeout_s):
        if self.live and name in self.live_tools:
            ok, data = await self.live.call(name, args, timeout_s)
            if name in AUTH_TOOLS:
                print(f"  [live {name}] ok={ok} shape={json.dumps(shape(data), ensure_ascii=False)}")
            elif name in BOOKING_TOOLS:
                print(f"  [LIVE {name}] {json.dumps(args, ensure_ascii=False)} → ok={ok} "
                      f"{json.dumps(data, ensure_ascii=False, default=str)[:300]}")
            return ok, data
        ok, data = await self.fake.call(name, args, timeout_s)
        if name not in AUTH_TOOLS:
            print(f"  [simulated {name}] {json.dumps(args, ensure_ascii=False)} → "
                  f"{json.dumps(data, ensure_ascii=False, default=str)[:200]}")
        return ok, data


class ConsoleOutput:
    def __init__(self):
        self.t0 = 0.0
        self.first = True

    async def say(self, text, *, language, interruptible=True):
        stamp = f"{(time.perf_counter() - self.t0) * 1000:5.0f}ms" if self.first else "       "
        self.first = False
        print(f"  🤖 {stamp}  {text}", flush=True)

    async def transfer(self, reason):
        print(f"  ↪ TRANSFER to human: {reason}")

    async def hangup(self):
        print("  ☎ call ended")


async def main(script: Path | None, offline: bool, verbose: bool, live_auth: bool, live_booking: bool) -> None:
    bus = EventBus()
    bus.add_redactor(redact_event)
    bus.subscribe(JsonlSink(get_settings().log_dir))
    if verbose:
        bus.subscribe(ConsoleSink(min_level=Level.INFO))
    await bus.start()

    live = None if offline else MCPPool(get_settings().mcp_server_config())
    if live_auth and offline:
        raise SystemExit("--live-auth needs the live HIS (drop --offline)")
    if live_booking:
        if not live_auth:
            raise SystemExit("--live-booking needs --live-auth (a real booking needs the real patient file)")
        answer = input("⚠ --live-booking creates REAL appointments in the HIS for the verified patient. Type yes: ")
        if answer.strip().lower() != "yes":
            raise SystemExit("cancelled")
    backend = HybridMCP(live, fake_backend(), live_auth, live_booking)
    print("connecting…" if live else "offline mode (all tools mocked)")
    executor = await build_tooling(backend)
    llm = create("llm", "groq")
    session = Session(call_id=f"chat-{int(time.time())}")
    out = ConsoleOutput()
    agent = Agent(session, executor, llm, FileSkillSet(executor.catalog), out, bus.bind())
    who = "your real mobile (real OTP via WhatsApp)" if live_auth else "test mobile 0500000001 · OTP 1234"
    booking = "bookings are REAL" if live_booking else "bookings are simulated"
    print(f"LLM {llm.settings.model} · {who} · {booking} · Ctrl+C to quit\n")

    out.t0 = time.perf_counter()
    await agent.start()
    lines = script.read_text(encoding="utf-8").splitlines() if script else None
    try:
        while not (session.ended or session.handoff):
            if lines is not None:
                if not lines:
                    break
                text = lines.pop(0).strip()
                if not text or text.startswith("#"):
                    continue
                print(f"\n👤 {text}")
            else:
                text = (await asyncio.to_thread(input, "\n👤 ")).strip()
                if not text:
                    continue
            out.t0, out.first = time.perf_counter(), True
            await agent.handle(text)
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        print(f"\nsession: verified={session.auth.verified} skill={session.active_skill} slots={session.slots}")
        await backend.close()
        await bus.stop()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", type=Path)
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true", help="print harness events")
    ap.add_argument("--live-auth", action="store_true", help="real patient lookup + OTP for your own number")
    ap.add_argument("--live-booking", action="store_true", help="REAL booking + confirmation in the HIS")
    a = ap.parse_args()
    asyncio.run(main(a.script, a.offline, a.verbose, a.live_auth, a.live_booking))
