"""Tool backends for development: live HIS for safe lookups, fake data for patient / write tools.

    TOOLS_MODE=live    → every tool goes to the HIS (production)
    TOOLS_MODE=hybrid  → non-patient lookups live; auth + writes simulated unless HYBRID_LIVE_AUTH /
                         HYBRID_LIVE_BOOKING are set (default for local testing)
    TOOLS_MODE=mock    → nothing leaves the machine
"""

import json
import logging
from typing import Any

from .mcp_client import MCPPool
from .mock_mcp import MockMCP

log = logging.getLogger(__name__)

LIVE_SAFE = {
    "mssql_get_Projects_from_Location", "mssql_get_clinics_for_project",
    "mssql_get_TopFive_nearestClinic_have_doctorSlots", "mssql_get_nearestClinic_have_doctorSlots",
    "mssql_get_TopFive_availableDoctors_with_slots_byDate", "mssql_get_availableDoctors_with_slots_byDate",
}
AUTH_TOOLS = {"mssql_get_patient_info", "api_send_otp_request", "api_verify_otp"}
BOOKING_TOOLS = {"api_book_Appointment", "mssql_confirm_appointment"}

TEST_MOBILES, TEST_OTP = {"0500000001", "0551234567"}, "1234"   # 0551234567: varied digits survive STT


def fake_backend() -> MockMCP:
    mock = MockMCP()
    mock.on("mssql_get_patient_info", lambda a: {"success": True, "count": 1, "patients": [
        {"patient_id": 900001, "full_name_en": "Mohammed Test", "gender": "M", "dob": "1990-03-15"}]}
        if a.get("mobileNo") in TEST_MOBILES else {"success": True, "count": 0, "patients": []})
    mock.on("api_send_otp_request", {"success": True, "message": "OTP sent"})
    mock.on("api_verify_otp", lambda a: {"success": str(a.get("Otp")) == TEST_OTP, "message": ""})
    mock.on("api_book_Appointment", lambda a: {"success": True, "message": "simulated", "appointment_data": {
        **{k: a.get(k) for k in ("ProjectID", "ClinicID", "DoctorID", "StartTime")},
        "AppointmentDate": a.get("StrAppointmentDate"), "AppointmentNo": "7700001"}})
    mock.on("mssql_confirm_appointment", "Status: Successfully Confirmed (simulated)")
    for name in ("api_send_AppointmentWhatsapp", "api_send_AppointmentSms", "api_send_ProjectLocationWhatsapp",
                 "api_send_ProjectLocationSms"):
        mock.on(name, {"success": True, "message": "simulated"})
    return mock


class HybridMCP:
    """Live HIS for selected tools, fake data for everything else. Prints every simulated / live write."""

    def __init__(self, live: MCPPool | None, fake: MockMCP | None = None, *, live_auth: bool = False,
                 live_booking: bool = False, echo=print) -> None:
        self.live, self.fake, self.echo = live, fake or fake_backend(), echo
        self.live_tools = LIVE_SAFE | (AUTH_TOOLS if live_auth else set()) | (BOOKING_TOOLS if live_booking else set())

    def scoped(self, names: set[str]) -> "HybridMCP":
        """The same simulation rules over one project's MCP servers only."""
        import copy
        view = copy.copy(self)
        view.live = self.live.scoped(names) if self.live is not None and hasattr(self.live, "scoped") else self.live
        return view

    async def start(self) -> None:
        if self.live:
            await self.live.start()

    async def close(self) -> None:
        if self.live:
            await self.live.close()

    def schemas(self) -> dict[str, dict[str, Any]]:
        # the HIS tools come from the fake (same schemas); tools of other servers added from the console are live
        live = self.live.schemas() if self.live else {}
        return {**live, **self.fake.schemas()}

    def status(self) -> dict[str, dict[str, Any]]:
        return self.live.status() if self.live and hasattr(self.live, "status") else {}

    async def call(self, name: str, args: dict[str, Any], timeout_s: float) -> tuple[bool, Any]:
        if self.live and name not in self.fake.schemas():
            return await self.live.call(name, args, timeout_s)
        if self.live and name in self.live_tools:
            ok, data = await self.live.call(name, args, timeout_s)
            if name in AUTH_TOOLS and self.echo:
                self.echo(f"  [live {name}] ok={ok} shape={json.dumps(shape(data), ensure_ascii=False)}")
            elif name in BOOKING_TOOLS and self.echo:
                self.echo(f"  [LIVE {name}] {json.dumps(args, ensure_ascii=False)} → ok={ok} "
                          f"{json.dumps(data, ensure_ascii=False, default=str)[:300]}")
            return ok, data
        ok, data = await self.fake.call(name, args, timeout_s)
        if name not in AUTH_TOOLS and self.echo:
            self.echo(f"  [simulated {name}] {json.dumps(args, ensure_ascii=False)} → "
                      f"{json.dumps(data, ensure_ascii=False, default=str)[:200]}")
        return ok, data


def shape(value: Any, depth: int = 0) -> Any:
    """Field names and types only — never values (safe to print for patient data)."""
    if isinstance(value, dict):
        return {k: shape(v, depth + 1) for k, v in value.items()} if depth < 4 else "{…}"
    if isinstance(value, list):
        return [shape(value[0], depth + 1), f"×{len(value)}"] if value else []
    return type(value).__name__
