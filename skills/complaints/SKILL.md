---
name: complaints
description: Create a complaint or suggestion about a visit, or check complaint status
keywords: [شكوى, شكوي, اشتكي, ابي اشتكي, ملاحظة, اقتراح, complaint, complain, feedback, suggestion]
extra_tools: [mssql_get_appointment_history]
status: draft   # flow spec pending
---
Listen with empathy and keep it short.
- New complaint: ask which hospital and clinic it concerns (use the appointment history if needed:
  mssql_get_appointment_history) and what happened. Summarise it as a short title and a description in the caller's
  words, then call api_create_complaint — the system asks you to read the summary back and confirm first.
  Give the caller the reference number from the result.
- Status of previous complaints → api_get_complaint_history (read at most 3, latest first).
