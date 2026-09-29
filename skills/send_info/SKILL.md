---
name: send_info
description: Send appointment details or the hospital location by SMS or WhatsApp
keywords: [ارسل, ارسلي, رسالة, واتساب, واتس, الموقع, لوكيشن, وين المستشفى, تفاصيل الموعد, send, whatsapp, sms, location, address, directions, appointment details]
extra_tools: [mssql_get_upcoming_appointment]
status: draft   # flow spec pending
---
Send appointment details or the hospital location to the caller.
Both need an appointment: call mssql_get_upcoming_appointment first and, if there are several, ask which one.
Ask the channel if the caller did not say it (WhatsApp or text message).
- Appointment details → api_send_AppointmentWhatsapp or api_send_AppointmentSms
- Hospital location → api_send_ProjectLocationWhatsapp or api_send_ProjectLocationSms
If the caller has no appointment, explain that the location can be sent for an existing appointment and offer
to book one.
