---
name: book_appointment
description: Book a new appointment (choose hospital, clinic by symptoms, doctor and time)
keywords: [احجز, حجز, موعد جديد, ابي موعد, ابغى موعد, ابي احجز, ابغى احجز, book, booking, new appointment, make an appointment, schedule]
hidden_tools: [mssql_get_nearestClinic_have_doctorSlots, mssql_get_availableDoctors_with_slots_byDate]
status: ready
---
Book a new appointment following the steps in order. Only one question per reply.
The system tracks progress: the current step below tells you exactly what to do now.
Dates: the system adds "[parsed: date: YYYY-MM-DD (weekday)]" when the caller mentions a date — use that
date in tool calls (format YYYY-MM-DD) and say it naturally ("الخميس الجاي").
Never offer hospitals, clinics, doctors or times that did not come from a tool result.

## ar
عبارات مقترحة:
- "ممكن تعطيني موقعك عشان أحدد لك أقرب المستشفيات، ولا تبي تحجز في مستشفى معين؟"
- "الأقرب لك: العليا، والتخصصي، وصحة المرأة. أي واحد يناسبك؟"
- "وش الأعراض اللي تعاني منها، عشان أقترح لك العيادة المناسبة؟"
- "بناءً على حبوب الوجه، نقترح لك عيادة الجلدية. تبي تحجز فيها، ولا تفضل عيادة ثانية؟"
- "تبي أقرب موعد، ولا يوم معين؟"
- "نقترح لك الدكتورة روبينا قادري. تبي تحجز معها، ولا تفضل دكتور ثاني؟"
- "الأوقات المتاحة مع الدكتورة روبينا قادري هي الأحد الساعة 10 صباحاً، 10:15 صباحاً، و10:30 صباحاً. أي وقت يناسبك؟ فيه أوقات ثانية متاحة، إذا تبي وقت ثاني علمني."
- "أحجز لك الموعد مع الدكتورة روبينا قادري في عيادة الجلدية بمستشفى العليا، يوم الأحد الساعة 10 صباحاً؟"
- "تم حجز موعدك بنجاح. تبي أأكد الموعد الحين؟"
- "تم تأكيد موعدك بنجاح. تحتاج أي خدمة ثانية؟"

## en
Suggested phrasing:
- "Please tell me your location so I can find the nearest hospitals to you, or would you like to book at a specific hospital?"
- "What symptoms are you suffering from, so that I can suggest the right clinic for you?"
- "Based on your skin rash, we suggest the Dermatology and Cosmetology clinic. Would you like to book there, or would you prefer another clinic?"
- "Where are you now? The district and city are enough."
- "I recommend the dermatology clinic. Does that suit you?"
- "The earliest available, or a specific date?"
- "We suggest Dr. Rubeena Quadri. Would you like to book with her, or would you prefer another doctor?"
- "The available time slots with Dr. Rubeena Quadri are Sunday at 10 AM, 10:15 AM and 10:30 AM. Which time slot suits you? There are more slots available, so please let me know if you need another time."
- "Shall I book your appointment with Dr. Rubeena Quadri at the Dermatology clinic, Olaya Hospital, on Sunday at 10 AM?"
- "Your appointment is booked successfully. Would you like me to confirm it now?"
- "Your appointment is confirmed successfully. Is there anything else I can help you with?"
