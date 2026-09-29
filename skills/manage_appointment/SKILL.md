---
name: manage_appointment
description: Existing appointments — list upcoming, confirm, cancel or reschedule
keywords: [مواعيدي, موعدي, اكد الموعد, تاكيد الموعد, الغي الموعد, الغاء الموعد, كنسل, اغير الموعد, تغيير الموعد, اأجل, upcoming, my appointment, confirm my appointment, cancel, reschedule, change my appointment]
status: draft   # flow spec pending (planned with the appointment-management use case)
---
Help with the caller's existing appointments.
1. Call mssql_get_upcoming_appointment. If there are several, read at most 3 (clinic, doctor, day and time) and ask
   which one. If none, say so and offer to book a new one.
2. Confirm → call mssql_confirm_appointment after the caller says yes.
3. Cancel → call mssql_cancel_appointment; the system asks you to read the appointment back and confirm first.
4. Reschedule → call mssql_reschudule_getNearestDoctorSlot for the same doctor, offer 3 times, then call
   api_Rescheduling_book_appointment; the system asks for read-back confirmation first.
Always use AppointmentNo (not SetupID) from the appointment details.
