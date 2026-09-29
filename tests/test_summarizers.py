"""Summarizers, using response shapes captured from the live HIS (scripts/probe_mcp.py)."""

import json

from runtime.tools import summarizers  # noqa: F401 — registers summarizers
from runtime.tools.summarize import to_llm_content

SLOTS = {
    "success": True, "count": 3, "page": 1, "pageSize": 5, "projectId": 12, "clinicId": 1,
    "hasNextPage": True, "hasPreviousPage": False,
    "doctors": [
        {"doctorId": "951", "doctorName": "cmc package booking", "doctorNameN": ".",
         "2026-09-28": [{"startTime": "08:00", "endTime": "09:00"}]},
        {"doctorId": "41", "doctorName": "NURSING SERVICE / TECHNICIAN", "doctorNameN": "NURSING SERVICE / TECHNICIAN",
         "2026-09-28": [{"startTime": "09:00", "endTime": "09:15"}]},
        {"doctorId": "5018", "doctorName": "RUBEENA QUADRI ", "doctorNameN": "روبينا قادري",
         "2026-09-28": [{"startTime": f"{h:02d}:00", "endTime": "x"} for h in range(8, 23)]},
    ],
}


def test_doctor_slots_drop_pseudo_doctors_and_cap_times():
    out = json.loads(to_llm_content("mssql_get_TopFive_availableDoctors_with_slots_byDate", SLOTS))
    assert [d["doctor_id"] for d in out["doctors"]] == [5018]
    doc = out["doctors"][0]
    assert doc["name_ar"] == "روبينا قادري" and doc["name_en"] == "Rubeena Quadri"
    [(day, times)] = doc["slots"].items()
    assert day.startswith("2026-09-28 الاثنين/Monday")   # weekday labelled so the LLM never guesses it
    assert times[0] == "08:00 (8 AM / 8 صباحاً)" and times[-1].startswith("+3 more, last ") and len(times) == 13
    assert out["has_next_page"] is True


def test_nearest_clinic_variant_uses_clinics_key():
    data = {**SLOTS, "clinics": SLOTS["doctors"]}
    del data["doctors"]
    out = json.loads(to_llm_content("mssql_get_TopFive_nearestClinic_have_doctorSlots", data))
    assert [d["doctor_id"] for d in out["doctors"]] == [5018]


def test_clinics_filtered_and_compact():
    data = [{"ClinicID": 5, "ClinicDescriptionN": "الأمراض الجلدية والتجميل"},
            {"ClinicID": 647, "ClinicDescriptionN": "طوارئ"},
            {"ClinicID": 134, "ClinicDescriptionN": "Blood Bank"},
            {"ClinicID": 640, "ClinicDescriptionN": "Livecare PEDIATRIC NEUROLOGY"},
            {"ClinicID": 180, "ClinicDescriptionN": "الطبيب طلب بنفسه"}]
    assert to_llm_content("mssql_get_clinics_for_project", data) == "clinics (id:name): 5:الأمراض الجلدية والتجميل"


def test_nearby_projects_cleaned(monkeypatch):
    from runtime.data import reference
    data = {"success": True, "projects": [{"ProjectID": 12, "ProjectNameN": "مستشفى العليا.\r\n",
                                           "Latitude": "24.7\r\n", "distance_km": 1.09}]}
    monkeypatch.setattr(reference, "_ref", None)   # no reference data: HIS name, cleaned
    out = json.loads(to_llm_content("mssql_get_Projects_from_Location", data))
    assert out == {"hospitals": [{"project_id": 12, "name_ar": "مستشفى العليا", "distance_km": 1.09}]}


def test_nearby_projects_named_from_reference(monkeypatch):
    """English calls get an empty HIS name; names come from the projects table instead."""
    from runtime.data import reference

    from .test_local_tools import REF
    monkeypatch.setattr(reference, "_ref", REF)
    data = {"projects": [{"ProjectID": 12, "ProjectNameN": "", "distance_km": 1.09}]}
    [h] = json.loads(to_llm_content("mssql_get_Projects_from_Location", data))["hospitals"]
    assert h["name_en"] == "Olaya Hospital" and h["name_ar"] == "مستشفى العليا"


def test_unexpected_shapes_pass_through():
    assert json.loads(to_llm_content("mssql_get_TopFive_availableDoctors_with_slots_byDate", {"error": "x"})) == {"error": "x"}
    assert to_llm_content("mssql_get_clinics_for_project", "no data") == "no data"


def test_nearby_hospitals_three_nearest():
    data = {"projects": [{"ProjectID": i, "ProjectName": f"H{i}", "distance_km": d}
                         for i, d in [(1, 5.0), (2, 1.2), (3, 9), (4, 0.4), (5, 3)]]}
    out = json.loads(to_llm_content("mssql_get_Projects_from_Location", data))
    assert [h["project_id"] for h in out["hospitals"]] == [4, 2, 5]


def test_short_doctor_names_and_spoken_times():
    from runtime.tools.summarizers import label_time, short_name
    assert short_name("Alaa Farez Mahmoud Esmaeil") == "Alaa Esmaeil"
    assert short_name("Mohammed Al Asiri") == "Mohammed Al Asiri"
    assert short_name("عبد الله سعد القحطاني") == "عبد الله القحطاني"
    assert label_time("14:30") == "14:30 (2:30 PM / 2:30 مساءً)" and label_time("08:00") == "08:00 (8 AM / 8 صباحاً)"
