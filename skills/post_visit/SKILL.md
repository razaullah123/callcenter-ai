---
name: post_visit
description: After a visit — ask the doctor to call back, ask about medication or results, prescriptions list
keywords: [يتصل علي, يكلمني, اتصال من الدكتور, الدكتور يتصل, الوصفة, الوصفات, الدواء, الادوية, العلاج, بعد الزيارة, call me back, callback, prescription, prescriptions, medication, medicine, ask the doctor]
status: draft   # flow spec pending
---
Help after a visit. Never give medical advice or explain medicines yourself — questions go to the doctor.
1. Find the visit/doctor: call mssql_get_appointment_history (or mssql_get_GroupedDoctor_Appoinments) and confirm
   which doctor and clinic (read at most 3, latest first).
2. Request type:
   - Doctor call-back → api_get_askingto_returncall
   - Question about medication → api_get_askingfor_medicine
   - Question about a result → api_get_askingfor_result
   - Other question → api_get_asking_others
   Pass ClinicID, DoctorID and a short Description of what the caller wants (in their words).
   Ask the caller to confirm before submitting.
3. Prescriptions list → api_get_prescription_appt_list; offer to send them (medical_reports, report type 3).
