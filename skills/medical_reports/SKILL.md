---
name: medical_reports
description: Lab results, radiology, medical reports, sick leave, discharge, vaccines, dental plan — sent by email or WhatsApp
keywords: [تحليل, تحاليل, نتيجة, نتايج, مختبر, اشعة, تقرير, تقرير طبي, اجازة مرضية, سك ليف, خروج, تطعيم, تطعيمات, لقاح, اسنان, lab, results, radiology, x-ray, report, medical report, sick leave, discharge, vaccine, dental]
status: draft   # flow spec pending
---
Help the caller get a report sent to them. Never read medical results or values aloud — reports are only sent.
1. Find out which report type they need, then list the available ones with the matching tool:
   lab → api_get_patient_lab_reports / api_get_lab_Result_for_patient · radiology → api_get_radiology_report_for_patient ·
   outpatient medical report → api_get_out_patient_medical_report · inpatient → api_Get_INPatient_Medical_Report ·
   sick leave → api_get_sickleavefor_patient · discharge → api_get_discharge_info · vaccines → api_get_done_vaccines ·
   dental plan → api_Dental_Treatment_Plan.
2. If several, read at most 3 (date and clinic) and ask which one.
3. Ask email or WhatsApp, then call api_send_medical_report_email with ReportID, ProjectID, Reporttype
   (1 lab, 2 radiology, 3 prescriptions, 4 outpatient, 5 inpatient, 6 discharge, 7 sick leave, 9 vaccine,
   10 dental) and channel_id (1 email, 2 WhatsApp), plus OrderNo / EpisodeID / AdmissionNo when the type needs it.
If a medical report does not exist yet, api_new_medical_report can request one (confirm with the caller first).
