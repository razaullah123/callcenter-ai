# Hamsa parity review

Section-by-section comparison of Hamsa's agent docs (https://docs.tryhamsa.com/agents/...) with our console + runtime.
One section per module; each component has a status and the code that implements it. Updated as we go.

Status: ✅ matches · 🟡 partial / differs · ❌ missing · ➕ ours only

Review order: **1 Dashboard** (done) → 2 Voice Agents → 3 Single Prompt Agent → 4 Flow Agent → 5 Publishing →
6 Variable System → 7 Tools → 8 Knowledge Base → 9 Batch Calls → 10 Call History → 11 Phone Numbers → 12 Webhooks →
13 Telephony Dashboard → 14 Testing Dashboard.

---

## 1. Dashboard  (reviewed 2026-10-06)

Hamsa pages: introduction, overview, performance, satisfaction, live-calls.
Ours: `console/src/pages/Dashboard.tsx` (UI) · `GET /api/dashboard` in `runtime/control/api.py:149` ·
`runtime/control/store.py::dashboard` (SQL) · `runtime/control/live.py` (live hub) · `console/src/DateRange.tsx`.

### 1.0 Shell / filters

| Hamsa component | Status | Ours / note |
|---|---|---|
| Landing page after login, in main nav | ✅ | `App.tsx` route; tab + preset + agent kept in the URL |
| 4 tabs: Overview, Performance, Satisfaction & Outcome, Live Calls | ✅ | `TABS` in Dashboard.tsx |
| Agent filter (All agents / one agent) | ✅ | `AgentPicker` |
| Date presets Today, Yesterday, This Week, This Month, Custom | ✅ | `DateRange.tsx` (+ ➕ "Last hour"); week starts Sunday |
| Browser-local timezone | ✅ | `tz` param, series bucketed `AT TIME ZONE tz` |
| Export | ➕ | CSV export of all metrics + series (not documented by Hamsa) |
| Auto refresh | ➕ | 15 s query refetch on stats tabs |
| "No Data Available" empty state | ✅ | `NoData` |

### 1.1 Overview

| Component | Status | Ours / note |
|---|---|---|
| Live Sessions (concurrent calls now) | ✅ | `live_calls` from the in-memory live hub |
| Total Sessions in window | 🟡 | We count every call **started** in the window (incl. still-running); Hamsa says completed. Minor |
| Avg Session Duration (`2m 34s`) | ✅ | `fmtDur`, avg of `ended_at - started_at` |
| Total Session Duration (`52h 18m`) | ✅ | same |
| Calls by Duration Bucket (<30s, 30-120s, >120s) | ✅ | card + pie chart; same boundaries |
| Avg Words per AI Response | ✅ | per turn, from `agent.say` events |
| Forwarded to Human Agent % | ✅ | `handoff IS NOT NULL` |
| Calls Over Time (hourly Today/Yesterday, daily week/month, auto for custom) | ✅ | hourly when window ≤ 2 days, else daily; empty buckets filled with 0 |
| Guidance bands (<30 s sessions warn, words <20 / >80, forwarded <10 / >20 %) | ✅ | Done 2026-10-06 (gap A) — see worklog |

### 1.2 Performance

| Component | Status | Ours / note |
|---|---|---|
| ASR Processing Time | ✅ | avg `stt.result` latency |
| LLM Response Time | ✅ | avg `llm.first_token` |
| TTS Generation Time | ✅ | per turn: first audio − LLM first token |
| Latency (ASR+LLM+TTS+network) | 🟡 | Ours is measured turn-end → first audio (true end-to-end, incl. end-of-turn silence), with p50/p95 — better than a sum, but the number is not comparable to Hamsa's thresholds |
| Error Rate | ✅ | distinct calls with an `error` event / calls |
| Tool-call time, processing-times bar chart | ➕ | Hamsa has neither |
| Threshold colours (ASR 150-300/500, LLM 1/2/3 s, TTS 400/600/800, latency 2/3/4 s, errors 1/3/5 %) | ✅ | All bands applied 2026-10-06 (gap A) |

### 1.3 Satisfaction & Outcome

| Component | Status | Ours / note |
|---|---|---|
| CSAT score | ❌ | Needs post-call survey / analysis (PLAN 12.9). UI says so. **Gap B** |
| NPS | ❌ | same. **Gap B** |
| Sentiment distribution (positive / neutral / negative donut) | ❌ | needs post-call LLM analysis. **Gap B** |
| First-Call Resolution = not transferred / total | ✅ | `pct(calls - handoffs, calls)` |
| Escalation Rate = escalated / total | ✅ | warn at >25 % (Hamsa bands: <10 excellent, 10-20 normal, 20-30 acceptable, >30 high) |
| Outcome distribution | ➕ | booked / verified only / handed to human / not resolved; plus booking & verification rate, languages, channels, hand-off reasons, where calls ended |

### 1.4 Live Calls

| Component | Status | Ours / note |
|---|---|---|
| List of in-progress calls, agent filter | ✅ | dashboard tab (table) + `pages/Live.tsx` |
| Call info: agent, start, elapsed, channel, "In progress" | ✅ | cards with agent, start, elapsed, channel, language, step (gap C, 2026-10-06) |
| Auto refresh 10 s | ✅ | WebSocket push (instant) |
| Only IN_PROGRESS; hide calls >10 min old | 🟡 | We drop a call 30 min after its last event (`ACTIVE_TTL_S`); Hamsa 10 min |
| Join as silent listener — live audio | ❌ | No audio tap for supervisors. **Gap D** |
| Live transcription with speaker labels | ✅ | drawer Conversation tab (gap C); text only |
| Live performance metrics (ASR/LLM/TTS/latency) | ✅ | drawer Overview: ASR / LLM / latency averages so far |
| Details drawer: Overview / Conversation / Outcome (summary, sentiment, resolution) | 🟡 | Overview / Conversation / Outcome tabs done (gap C); AI summary + sentiment in Outcome wait for post-call analysis (gap B) |
| Join / Leave call | ❌ | Same as D |
| End call from console | ➕ | `EndCall` in Live.tsx (Hamsa docs don't list it) |

### Gap list (proposed order)

- ~~**A. Threshold colours + hints** on Overview/Performance/Outcome metrics using Hamsa's bands — small, UI only.~~ done
- ~~**C. Live drawer**: channel + start time on rows, drawer with Overview / Conversation (speaker-labelled live transcript) / metrics — UI + reuse existing live WS.~~ done
- **B. Post-call analysis** (summary, sentiment, CSAT/NPS proxy) → fills Satisfaction tab and drawer Outcome — larger; belongs with PLAN 12.9 / webhooks.
- **D. Silent listen** (audio tap to the console) — largest; needs a media fan-out from the call loop. Decide whether wanted.
- Minor: align `ACTIVE_TTL_S` to 10 min (check IVR calls with long silences first); Total Sessions semantics.

Verdict: Overview ✅, Performance ✅ (thresholds partial), Outcome 🟡 (satisfaction metrics missing), Live Calls 🟡 (monitoring yes, join/listen no).

Work done on each item is logged in [`hamsa_parity_worklog.md`](hamsa_parity_worklog.md).
