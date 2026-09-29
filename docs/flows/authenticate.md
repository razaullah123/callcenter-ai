# Flow: Authenticate (runs first on every call)

Source: user specification, 2026-09-27. Implemented by `skills/authenticate/`.

**Hard rule:** no other skill (booking, reports, insurance, …) and no patient-data tool runs until the caller is verified by OTP.
The harness enforces this in the policy layer (`auth_gate`), not only in the prompt.

## Steps

```mermaid
flowchart TD
    G["Greeting (pre-recorded)<br/>مرحباً بكم في مجموعة الدكتور سليمان الحبيب. كيف اقدر اخدمك؟"] --> I[Caller states need<br/>→ stored as pending_intent]
    I --> M[Ask mobile number]
    M --> P[mssql_get_patient_info<br/>mobileNo, LanguageID]
    P -->|0 records| X["Please contact the support team to register your information first → end / handoff"]
    P -->|1 record| S[patient_id stored]
    P -->|multiple records| D[Ask date of birth + first name]
    D --> DM{Match exactly one record?}
    DM -->|yes| S
    DM -->|no| D2[Ask again once → else handoff]
    S --> O[api_send_otp_request<br/>Channel = WhatsApp]
    O --> OA[Ask caller for the OTP]
    OA --> V[api_verify_otp]
    V -->|valid| OK[verified = true<br/>→ resume pending_intent]
    V -->|invalid| R{"OTP incorrect — say it again,<br/>or shall I send it by SMS?"}
    R -->|re-enter| OA
    R -->|send by SMS| O2[api_send_otp_request<br/>Channel = SMS] --> OA
```

## Step details

| # | Step | Tool | Inputs | Outputs → state |
|---|------|------|--------|-----------------|
| 0 | Greeting | — (pre-recorded audio) | — | caller's first utterance → `pending_intent`, `language_id`, first gender estimate |
| 1 | Mobile number | — | spoken digits (Arabic / English, "صفر خمسة أربعة…") → normalized to local `05XXXXXXXX` (e.g. `054…`); `+9665…` / `9665…` / `5…` converted | `mobile_no` |
| 2 | Lookup | `mssql_get_patient_info` | `mobileNo`, `LanguageID`* | 0 / 1 / N records |
| 3 | Disambiguate (N>1 only) | — | date of birth (**Gregorian only**, → `YYYY-MM-DD`) + first name, matched against the returned records (Arabic + English names, normalized) | `patient_id` |
| 4 | Send OTP | `api_send_otp_request` | `PatientId`*, `Channel`=WhatsApp, `LanguageID`* | — |
| 5 | Verify OTP | `api_verify_otp` | `Otp` (spoken digits → normalized), `PatientId`*, `LanguageID`* | `verified` |
| 5b | Wrong OTP | `api_send_otp_request` (only if caller asks) | `Channel`=SMS | — |

\* injected by the harness from session state.

## Rules
- The caller's first utterance is kept as `pending_intent` so, after verification, the agent continues with what they asked for without asking again.
- No record → *"Please contact the support team to register your information first"* (Arabic: يرجى التواصل مع فريق الدعم لتسجيل بياناتك أولاً), then close politely / hand off.
- Record details (name, DOB, other family records) are never read out before verification.
- Limits (confirmed): 3 OTP attempts, 2 DOB / first-name attempts → human handoff.
- The whole call — prompts, tool `LanguageID`, TTS voice — follows the caller's language (Arabic → 1, English → 2).
