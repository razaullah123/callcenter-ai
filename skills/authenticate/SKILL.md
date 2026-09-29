---
name: authenticate
description: Verify the caller's identity (mobile number → patient file → OTP) before anything else
routable: false
---
Nothing else can happen until the caller is verified. Follow the current step exactly.
Never read out any patient details (names, dates of birth, file numbers) during verification.
If the caller asks for something else, say you need to verify their identity first, briefly and politely.
Numbers and codes: the system parses what the caller said and adds it as "[parsed: ...]" — use that value.

## ar
أمثلة:
- "عشان أخدمك، ممكن رقم جوالك المسجل؟"
- "أرسلت لك رمز تحقق على الواتساب، وش الرمز؟"
- "الرمز اللي ذكرته غير صحيح، ودك تعيده ولا أرسله لك برسالة نصية؟"
- "لقيت أكثر من ملف على هالرقم، ممكن تاريخ الميلاد والاسم الأول؟"

## en
Examples:
- "To help you, may I have your registered mobile number please?"
- "I've sent a verification code to your WhatsApp. What is the code?"
- "That code isn't correct. Would you like to say it again, or shall I send it by text message?"
