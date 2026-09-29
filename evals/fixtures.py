"""Deterministic fake HIS for evals: test patients + real response shapes (captured from the live HIS with
scripts/probe_mcp.py), re-dated relative to the run day so slots are always in the future."""

import copy
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from runtime.harness.nlu.dates import today_riyadh
from runtime.tools import MockMCP

DIR = Path(__file__).parent / "fixtures"

# mobile → patient records (as returned by mssql_get_patient_info)
PATIENTS: dict[str, list[dict[str, Any]]] = {
    "0551234567": [{"patient_id": 900001, "full_name_en": "Mohammed Alqahtani", "gender": "M", "dob": "1990-03-15"}],
    "0559876543": [{"patient_id": 900002, "full_name_en": "Noura Alotaibi", "gender": "F", "dob": "1994-07-01"}],
    "0550000002": [{"patient_id": 900003, "full_name_en": "Abdullah Alharbi", "gender": "M", "dob": "1985-11-20"},
                   {"patient_id": 900004, "full_name_en": "Sara Alharbi", "gender": "F", "dob": "2012-05-09"}],
}
OTP = "1234"


def _load(name: str) -> Any:
    return json.loads((DIR / f"{name}.json").read_text(encoding="utf-8"))["data"]


def _redate(data: dict, days: list[date]) -> dict:
    """Move each doctor's slots onto the given days (keeps times / doctors from the real response)."""
    out = copy.deepcopy(data)
    for entry in out.get("doctors") or out.get("clinics") or []:
        slot_keys = [k for k, v in entry.items() if isinstance(v, list)]
        times = [t for k in slot_keys for t in entry.pop(k)]
        per_day = max(1, len(times) // len(days))
        for i, d in enumerate(days):
            chunk = times[i * per_day:(i + 1) * per_day] if i < len(days) - 1 else times[i * per_day:]
            if chunk:
                entry[d.isoformat()] = chunk
    return out


def fixture_backend(record: list | None = None) -> MockMCP:
    """MockMCP serving fixture data. `record` collects (tool, args) for assertions."""
    mock = MockMCP()
    clinics_ar = _load("mssql_get_clinics_for_project")
    clinics_en = _load("mssql_get_clinics_for_project.en")

    def clinics(a):
        return clinics_en if str(a.get("LanguageID")) == "2" else clinics_ar
    nearby = _load("mssql_get_Projects_from_Location")
    nearest = _load("mssql_get_TopFive_nearestClinic_have_doctorSlots")
    by_date = _load("mssql_get_TopFive_availableDoctors_with_slots_byDate")
    today = today_riyadh()

    def patient_info(a):
        rows = PATIENTS.get(str(a.get("mobileNo")), [])
        return {"success": True, "count": len(rows), "patients": rows}

    def slots_by_date(a):
        try:
            d = date.fromisoformat(str(a.get("date"))[:10])
        except ValueError:
            return {"success": True, "count": 0, "doctors": []}
        if d.weekday() == 4:   # Friday: no clinics
            return {"success": True, "count": 0, "doctors": [], "hasNextPage": False}
        return _redate(by_date, [d])

    mock.on("mssql_get_patient_info", patient_info)
    mock.on("api_send_otp_request", {"success": True, "message": "OTP sent"})
    mock.on("api_verify_otp", lambda a: {"success": str(a.get("Otp")) == OTP,
                                         "message": "verified" if str(a.get("Otp")) == OTP else "invalid otp"})
    mock.on("mssql_get_Projects_from_Location", nearby)
    mock.on("mssql_get_clinics_for_project", clinics)
    mock.on("mssql_get_TopFive_nearestClinic_have_doctorSlots",
            lambda a: _redate(nearest, [today + timedelta(days=1), today + timedelta(days=2)]))
    mock.on("mssql_get_TopFive_availableDoctors_with_slots_byDate", slots_by_date)
    mock.on("api_book_Appointment", lambda a: {"success": True, "message": "The appointment is booked successfully.",
                                               "appointment_data": {**a, "AppointmentNo": "880001"}})
    mock.on("mssql_confirm_appointment", "Status: Successfully Confirmed")
    mock.on("mssql_get_upcoming_appointment", [])
    for name in ("api_send_AppointmentWhatsapp", "api_send_AppointmentSms", "api_send_ProjectLocationWhatsapp",
                 "api_send_ProjectLocationSms"):
        mock.on(name, {"success": True})
    return mock
