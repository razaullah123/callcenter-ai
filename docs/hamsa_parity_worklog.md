# Hamsa parity — work log

What was actually changed, per component. The review itself (what matches / what is missing) is in
[`hamsa_parity.md`](hamsa_parity.md); this file records the work done on it. Newest first.

## 2026-10-06 — Dashboard: gaps A and C

| Component | Gap | Change | Files |
|---|---|---|---|
| Overview metrics | A | Guidance notes + colour: calls < 30 s share (>30 % warns), avg words per reply (<20 short, 20-60 natural, >80 long), forwarded-to-human % | `console/src/thresholds.ts`, `pages/Dashboard.tsx` (`ListCard` takes a `band`) |
| Performance metrics | A | Hamsa bands for ASR (300/500 ms), LLM first token (1/2/3 s), TTS (400/600/800 ms), latency (2/3/4 s), error rate (1/3/5 %); replaces the two ad-hoc tones | same (`Metric` takes a `band`) |
| Satisfaction & Outcome metrics | A | FCR bands (>80 / 70-80 / 60-70 / <60 %), escalation bands (<10 / 10-20 / 20-30 / >30 %) | same |
| Live Calls list | C | Table → cards: agent, call id, started, elapsed (ticks each second), channel, language, step, last caller line, "In Progress" pill, **View** button | `pages/Dashboard.tsx::LiveCalls` |
| Live call drawer | C | Right-hand drawer (Esc / backdrop / ✕ closes): **Overview** (session id, status, start, duration, channel, language, step, turns, ASR / LLM / latency averages so far), **Conversation** (speaker-labelled chat bubbles with times, auto-scroll, refreshed every 2 s from `GET /api/calls/:id`), **Outcome** (escalated / resolved, verified, booked, hand-off reason, ended-by — shown once the call ends; the drawer stays open when the call leaves the active list) | `pages/LiveDrawer.tsx` |

Notes
- No backend change: channel comes from the call id prefix (`ivr-` / `chat-` / web), start time from `ActiveCall.started`.
- The drawer is text-only. **View**, not "Join": there is no audio to listen to (gap D).
- AI summary and sentiment in the Outcome tab, and the CSAT / NPS / sentiment cards, still wait for post-call analysis (gap B, PLAN 12.9).
- Verified with `npm run build` (tsc -b + vite), then **in Chrome** on a throw-away server (port 8092) with a text-chat call held open:
  - Overview notes show ("many calls under 30 s…", "may be too short", "excellent"); Outcome FCR / Escalation show "excellent".
  - Performance tab had no latency data in this database, so the ASR / LLM / TTS / latency bands are untested on screen (empty state OK).
  - Live card appeared with agent, start, elapsed (ticking), channel "Chat", language, step, last caller line; View opened the drawer.
  - Drawer Overview, Conversation (Agent / Caller bubbles, Arabic RTL) and Outcome ("appears once the call has ended" → "Resolved · Ended by chat_ended" after the call ended) all worked.
  - Found and fixed: drawer showed the raw language code "ar" → now "Arabic" (`LANG` moved into `LiveDrawer.tsx`).
  - Not testable this way: ASR / latency averages for typed chat are "—" (no STT / TTS); a voice call is needed.

Still open on the Dashboard: B (post-call analysis), D (silent listen), `ACTIVE_TTL_S` 30 → 10 min, "Total Sessions" counts started vs completed.
