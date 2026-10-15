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
| CSAT score | 🟡 | **Estimated** from each call's transcript by post-call analysis (see 14): share of calls rated 4-5, Hamsa's bands. Gap B done |
| NPS | 🟡 | **Estimated** (promoters minus detractors), see 14 |
| Sentiment distribution (positive / neutral / negative) | ✅ | per-call sentiment from post-call analysis; split shown with the number of analysed calls (a list, not a donut) |
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
| Join as silent listener — live audio | ✅ | "Listen in" card in the live drawer and the call panel, both sides, listen-only (section 15). **Gap D** done |
| Live transcription with speaker labels | ✅ | drawer Conversation tab (gap C); text only |
| Live performance metrics (ASR/LLM/TTS/latency) | ✅ | drawer Overview: ASR / LLM / latency averages so far |
| Details drawer: Overview / Conversation / Outcome (summary, sentiment, resolution) | ✅ | Overview / Conversation / Outcome tabs; the Outcome tab has the AI summary, sentiment, resolution (post-call analysis, section 14) |
| Join / Leave call | ✅ | Start listening / Stop (section 15); no voice join, by design |
| End call from console | ➕ | `EndCall` in Live.tsx (Hamsa docs don't list it) |

### Gap list (proposed order)

- ~~**A. Threshold colours + hints** on Overview/Performance/Outcome metrics using Hamsa's bands — small, UI only.~~ done
- ~~**C. Live drawer**: channel + start time on rows, drawer with Overview / Conversation (speaker-labelled live transcript) / metrics — UI + reuse existing live WS.~~ done
- ~~**B. Post-call analysis** (summary, sentiment, CSAT/NPS proxy)~~ done — section 14.
- ~~**D. Silent listen** (audio tap to the console)~~ done — section 15.
- Minor: align `ACTIVE_TTL_S` to 10 min (check IVR calls with long silences first); Total Sessions semantics.

Verdict: Overview ✅, Performance ✅ (thresholds partial), Outcome 🟡 (CSAT / NPS are estimates, section 14), Live Calls ✅ (monitoring + listening, section 15).

Work done on each item is logged in [`hamsa_parity_worklog.md`](hamsa_parity_worklog.md).

---

## 2. Agents  (2.1-2.5 reviewed 2026-10-06; the Agents module is done)

Hamsa pages: `agents/introduction`, `agents/single-prompt/*` (overview, write-prompt, call-behavior, voice-settings,
intelligence-features, configure-settings). Next: `agents/flow-agent/*` (+ 10 node pages), `agents/publishing/*`,
`agents/variables/*`.
Ours: `console/src/pages/Agents.tsx` (list, create, import, duplicate, delete) · `studio/GlobalSettings.tsx` (all
settings) · `runtime/platform/bundle.py::KNOBS` (per-agent knobs) · `runtime/control/agents_api.py` (agent types,
blank agents) · `runtime/skills/loader.py::template_vars` (prompt variables) · `runtime/harness/engine.py` (greeting).

### 2.1 Voice Agents (introduction / agent list)

| Hamsa component | Status | Ours / note |
|---|---|---|
| Two agent types: Single Prompt, Flow Agent | ✅ | `type: prompt / flow`; both creatable from the list (`CreateDialog`) |
| Build / test (browser + phone) / monitor / deploy (phone number or web SDK) / integrate / optimise | 🟡 | Studio + test panel, Playground, Live, phone routes all there. **Web SDK / embed** (Hamsa "Publishing → Embed") is not built — reviewed under Publishing |
| Agent management (create, duplicate, delete, search, filters, columns, import) | ➕ | Hamsa's page doesn't specify any; ours has all of them plus Hamsa-JSON import |

### 2.2 Single Prompt Agent

**Prompt (write-prompt)**

| Component | Status | Ours / note |
|---|---|---|
| Preamble / system prompt (identity, tone, guidelines, tasks, guardrails, edge cases) | ✅ | Global Settings → System prompt (+ full-screen editor), `## ar` / `## en` sections |
| Greeting message, static | ✅ | per-language, said word for word (`engine.py:162`) |
| Greeting message, **dynamic** (generated from context) | ✅ | greeting renders as a template (`{{ agent_name }}`, `{{ current_time }}`, `{{ user_number }}`…) — done 2026-10-06 (gap F). Not LLM-generated |
| Jinja2 in prompts | ✅ | sandboxed Jinja (`skills/flow.py::render`) |
| System variables `current_time`, `current_date`, `current_weekday`, `call_id`, `direction`, `user_number`, `user_number_area_code`, `agent_name`, `agent_number` | ✅ | all nine added to `template_vars` 2026-10-06 (gap E), next to the flow-side names (`current_datetime`, `call_lang`, `userNumber`, `callParams`). `direction` is always `inbound`; `agent_number` is empty until a transport supplies it; `user_number_area_code` is the first two digits of a Saudi national number |
| Custom variables via call `params` | 🟡 | typed/extracted variables exist in flows; no `params` on call start (IVR / web). Reviewed under Variable System |
| Enhanced Turn Taking (numerals, backchannel) | ❌ | not a knob (PLAN lists it as not built). **Gap G** |
| Prompt Enhancer (Disabled / Basic / Advanced) | ❌ | not built. **Gap G** |
| Setup guidance (>500 words / >3 tools → use Flow) | n/a | docs advice only |

**Call behaviour**

| Hamsa setting (range · default) | Status | Ours (range · default) |
|---|---|---|
| Response delay (100-1500 ms · 400) | 🟡 | `voice_end_silence_ms` 200-2000 · 550 — wider, different default |
| User inactivity timeout (5-60 s · 15) | 🟡 | `voice_inactivity_s` 0-60 · **0 = off** (Hamsa defaults to on) |
| Max call duration (30 s - 1 h · 5 min) | 🟡 | `call_max_minutes`, whole minutes 0-120 · 0 = no limit; can't express < 1 min |
| Interruption on/off (· on) | ✅ | `voice_interrupt` |
| Minimum interruption duration (0.2-1.5 s · 0.5) | 🟡 | `voice_barge_in_ms` 0.1-2 s · 0.3 |
| VAD activation threshold (0.2-0.9 · 0.5) | ✅ | `voice_vad_threshold`, same range / default |
| Thinking voice on/off | 🟡 | ours is a delay ("say a filler after N s", `voice_filler_after_s`), no on/off |
| Wait for user to speak first (Never / Always / Outbound) | ✅ | knob `voice_wait_for_user` + Call Settings control, 2026-10-06 (gap F). "Outbound" has no effect until we place outbound calls (batch calls module) |
| Ambient sound | ❌ | **Gap H** |
| Background noise vs noise cancellation conflict warning | n/a | we have no background-noise feature |

**Voice**

| Component | Status | Ours / note |
|---|---|---|
| Voice library (tabs, filters language / gender / dialect / style, search) | 🟡 | `/voices` page with catalogue + preview + filters (gender / dialect appear in `Voices.tsx`). No Favorites / Currently used / My voices tabs. To verify in the UI during the Voices review |
| Voice cloning | ❌ | provider feature; not exposed |
| Voice dictionaries (pronunciation) | ❌ | **Gap H** |
| Expressiveness 0-2 | ❌ | TTS settings are provider-specific JSON; no common control. **Gap H** |
| STT model choice | ✅ | `ModelPicker` per STT connection (Whisper models, not Hamsa-STT) |
| Speed not configurable | ✅ | same |

**Intelligence features**

| Feature | Status | Ours / note |
|---|---|---|
| User gender detection (toggle) | 🟡 | Arabic gender agreement is always on (no toggle, no "inject gender into context" for the prompt) |
| Smart call end (toggle + editable end-call prompt) | 🟡 | ends when the caller says goodbye (always on); not editable, no toggle |
| Language / dialect switcher | 🟡 | switches language when the caller does (always on); no dialect switching, no toggle |
| Speaker identification (beta) | ❌ | not planned (PLAN lists it) |
| Agentic RAG (agent decides when to search) | ✅ | KB items attach the `search_knowledge_base` tool the model calls itself; there is no "search every turn" mode to contrast |

**LLM / noise / integrations (configure-settings)**

| Component | Status | Ours / note |
|---|---|---|
| LLM provider / model / temperature (0-1 · 0.2) | ✅ | Global Settings → LLM Configuration (+ max reply tokens ➕); providers are Groq / OpenAI-compatible (no Gemini / DeepMyst types); default temp 0.3 |
| Noise cancellation model (Telephony / General), strategy (per call / per turn), auto gain control, send denoised to STT | ❌ | we have echo cancellation (phone), a level gate, VAD threshold and "interrupt only for real words" — no denoiser model. **Gap H** |
| Knowledge base attach | ✅ | Global Settings → Knowledge Base |
| Tools: API request, MCP | ✅ | `config/tools.yaml` / Tools Templates |
| Tools: webhook trigger | 🟡 | HTTP tools can POST anywhere; no dedicated webhook-tool type |
| Tools: Web tools (client-side JS, SDK) | ✅ | dashboard-only definitions; the site registers the function (`VoiceAgent.registerTools`). See "Web tools" below |
| Outcome parameters / post-call prompts | ✅ | Global Settings → Outcome: switches + your own typed fields (section 14) |
| Call webhook (all events + conversation) | 🟡 | ✅ `call.start` / `call.end` (summary + masked transcript) per agent, bearer secret, retries, test button — Global Settings → Call Webhook. No per-event types beyond start / end, no delivery history, no outcome data (needs post-call analysis) |
| Phone number assignment | ✅ | Global Settings → Phone Number, `/numbers` |

### Agents gap list (proposed order)

- ~~**E. Template variables** — add Hamsa's system variables to `template_vars` (`current_time`, `current_date`, `call_id`, `direction`, `user_number`, `user_number_area_code`, `agent_name`, `agent_number`); keep the old names. Small, backend + tests.~~ done
- ~~**F. Greeting and first speaker** — render the greeting as a template (dynamic greeting) and add "wait for user to speak first" (Never / Always / Outbound only). Medium; touches `engine.start` and the voice call loop.~~ done
- **I. Align ranges / defaults / units** with Hamsa where it's only a UI choice (max call duration in seconds, minimum interruption 0.2-1.5, inactivity default). Small; changes defaults for existing agents, so decide first.
- **G. Prompt settings** — Enhanced Turn Taking, Prompt Enhancer. Needs a design (what each does for us).
- **H. Audio extras** — ambient sound, noise-cancellation block, expressiveness, voice dictionaries. Provider-dependent; decide which providers support them first.
- Intelligence toggles (gender detection, smart call end + custom prompt, dialect switcher): expose the always-on behaviours as toggles with defaults = today's behaviour. Medium.
- Not planned / later modules: speaker identification, voice cloning.

Verdict: Voice Agents intro ✅. Single Prompt: prompt, LLM, KB, tools, phone, VAD, interruption ✅; call-behaviour ranges 🟡; intelligence toggles 🟡; greeting / first-speaker, noise cancellation, ambient, expressiveness, dictionaries, enhancer ❌.

### 2.3 Flow Agent  (reviewed 2026-10-06)

Hamsa pages: `flow-agent/overview`, `global-settings`, `global-nodes`, `transitions`, `dtmf`, `debugging`,
`best-practices`, and 10 node pages (`nodes/*`).
Ours: `runtime/skills/graph.py` (graph model, edge conditions, `_holds`) · `runtime/skills/flow.py` (templates, values) ·
`runtime/harness/engine.py` (~l.320: performs tool / transfer / end / skill nodes) · `runtime/voice/call.py::on_dtmf` ·
`console/src/studio/FlowCanvas.tsx` (canvas, node + transition inspectors, variables panel) ·
`studio/TestPanel.tsx` + `useTestCall.ts` (test call, logs) · `runtime/platform/hamsa_import.py`.

**Node types**

| Hamsa node | Status | Ours / note |
|---|---|---|
| Start node, conversation mode (static or prompt greeting, DTMF menu, extraction) | ✅ | the first node can be static or prompt, collect digits, and have key transitions; the agent greeting is separate (templatable) |
| Start node, tool mode (pre-fetch before talking) | 🟡 | a tool node can be the start node; no error behaviour / timeout / output override fields |
| Conversation node: prompt (dynamic) | ✅ | `instructions`, Jinja, per-node tools, auto-call tools, typed variable extraction (`extract`) |
| Conversation node: **static** message (fixed text) | ✅ | node mode "Static — said exactly" (`say` per language, `{{ }}` filled in, no model call), 2026-10-06 (gap K); an optional prompt takes over if the caller replies and no transition fires |
| Conversation node: DTMF input capture (1-20 digits, # / * end, 1-30 s timeout) | ✅ | `dtmf_capture` {variable, max_digits, end_keys, timeout_s}: the call collects digits, ends on an end key / the maximum / a pause, stores them in the variable and carries on; checkbox block in the node inspector (gap M, 2026-10-06). The log shows only the digit count |
| Conversation node: skip response | 🟡 | `skip_response` on static messages: said, then straight to the next node (gap Q). Not for AI-written (prompt) nodes |
| Router node (no LLM, local evaluation, AND / OR, Else) | ✅ | `router` node; `all` / `any`; an unconditional last edge is the Else |
| Router operators `== != > < >= <= contains not_contains exists not_exists regex` | ✅ | `equals`, `ne`, `gt`, `gte`, `lt`, `lte`, `contains`, `not_contains`, `regex`, `exists` / `not_exists` (= `filled` / `empty`), 2026-10-06 (gap J); form in the transition inspector, Hamsa import maps them, a bad regex is a graph error |
| Tool node: pick tool, map parameters (`{{var}}`), JSONPath output mapping | ✅ | `tool` + `args` (templates) + `outputs` (paths) |
| Tool node: on success / on failure paths | ✅ | edges `on: success / failure` |
| Tool node: error behaviour (continue / retry / fail), timeout, custom spoken response, processing message | ✅ | `on_error` (continue = follow the failure edge · retry = `retries` extra attempts · fail = hand to a person), `timeout_s` (a write already under way is never abandoned), `processing` line, `say` after success with `{{ }}` (gap O, 2026-10-06) |
| Web tool node (client-side JS via SDK) | ✅ | a flow tool node can call a web tool (failure edge if the page lacks it). See "Web tools" below |
| Transfer call node (E.164 number, cold / warm, message, timeout 1-60 s, SIP headers) | ✅ | `destination` (E.164 / extension / template, validated), `transfer_type` warm (announce, default) / cold (silent, at once), announcement per language, `timeout_s` 1-60, SIP `headers`; sent to the IVR as `destination` / `timeout_s` / `headers` (gap N, 2026-10-06). Whether the phone system honours timeout / headers is up to it (docs/ivr_protocol.md) |
| Transfer agent node (other Hamsa agent, optional history + variables handoff) | ✅ | `agent` node: picks an agent of the project (or a `{{ }}` id), says its line, then the call's agent is swapped — the next agent's models, voice, skills, tools and call settings take over; `handoff_history` / `handoff_variables` choose what it inherits (no new greeting when the conversation comes along); failure (unknown agent, not published, > 3 hand-offs per call) follows the "On failure" edge or goes to a person; works on phone / browser calls and chat; the call record follows the new agent (gap R, 2026-10-06) |
| End call node (static / prompt message, variables, silent end) | 🟡 | `end` with `say` per language (now rendered as a template, gap K), silent if empty; no prompt-written (LLM) goodbye |
| Set local variables node (typed values, `{{var}}`) | ✅ | `set` node (`"=0"` literals, templates, `null` forgets) |
| Change agent settings node (system prompt, voice, expressiveness, STT, call settings, from here on) | 🟡 | `settings` node: system prompt, voice per language, STT model, LLM model + temperature, call settings (interrupt, response delay 100-1500 ms, inactivity 5-60 s, min interruption, VAD 0.2-0.9); cumulative until another settings node changes them, an empty value withdraws one (gap P, 2026-10-06). Expressiveness and voice dictionaries: no provider support yet (gap H) |
| `skill` node (continue in another skill) | ➕ | ours only |

**Transitions**

| Hamsa type | Status | Ours / note |
|---|---|---|
| Prompt (natural language, LLM-evaluated) | ✅ | `when: {llm: …}` classifier |
| Equation (variables, AND / OR) | ✅ | all operators above, combined with `all` / `any` |
| Always | ✅ | edge without a condition |
| Auto (advance on completion) | ✅ | same; tool / set nodes advance on their own |
| DTMF (keys 0-9 * #, menus; with input capture only # and *) | ✅ | `when: {dtmf: "1"}` transitions + keypad picker in the transition inspector; on a node that collects digits, digits go to the capture and * / # (when not end keys) can still be transitions (gap M) |
| Evaluated in order, first match wins | ✅ | own edges then global ones; reorder with ▲▼ |
| Every conditional node needs an Always fallback | 🟡 | the validation badge warns for routers (and for tool nodes without a failure path); conversation nodes may stay put by design |
| ➕ | | `replied`, `stage`, `not_all_filled`, tool `result` conditions |

**Global nodes**

| Hamsa setting | Status | Ours / note |
|---|---|---|
| Node reachable from anywhere (conversation, transfer agent, end call types) | ✅ | global edges (`from: "*"`); any node type |
| Trigger: natural language | ✅ | `when: {llm}` on a `*` edge |
| Trigger: DTMF key | ✅ | a `*` edge with `dtmf` (gap M) |
| Return to source after running (`globalReturnToSource`) | ✅ | global edge option `back`: once the caller has replied to the global step (or at once for a non-conversation step) the call returns to the step they interrupted (gap Q) |
| Require double confirmation | ✅ | edge option `confirm`: the agent asks "Just to confirm…" (phrase `CONFIRM_GLOBAL`, or the edge's own question per language); only a yes takes the edge (gap Q) |
| Skip response | ✅ | edge option `silent`: End → no goodbye, transfer → no hand-off line, conversation → nothing said and it moves on (gap Q) |
| Conditions only on system / custom variables (not extracted) | n/a | ours may use any slot |

**Global settings and variables (flow level)**

| Hamsa item | Status | Ours / note |
|---|---|---|
| System prompt, voice, LLM, noise, KB, MCP tools, outcome, phone, call settings, webhook sections | ✅ / 🟡 | same Global Settings panel as Single Prompt (see 2.2 for the gaps) |
| Node-level overrides of those settings (except the system prompt) | ✅ | through the settings node (gap P) |
| Per-node LLM model selection | ✅ | conversation node "Model for this step" (model + temperature of the agent's language-model connection) and the settings node (gap P) |
| Variable types: system / extracted / custom | 🟡 | system ✅ (gap E), extracted ✅ (`variables` + `extract`), custom `params` from call start ❌ — Variable System review |
| Variable name rules (snake_case) | 🟡 | not enforced; ours allows any slot name |

**Debugging and validation**

| Hamsa tool | Status | Ours / note |
|---|---|---|
| Validation engine with header badge (OK / warnings / errors, "Focus" jumps to the node) | ✅ | live badge on the canvas (`✓ Valid` / `⚠ n` / `✕ n`) with a list and Focus buttons (`studio/validate.ts`), 2026-10-06 (gap L). Errors: missing start / targets / tool / skill, undeclared variables, empty question, bad regex. Warnings: no prompt, empty static message, router without fallback, tool without failure path, no way out, unreachable node, unknown condition. Not covered: Hamsa's tool-override and global-node-variable checks |
| Test call from the header with live node highlight | ✅ | browser call + chat tests, canvas follows the active node |
| Call logs panel (node enter / exit, transitions, extracted variables, tool request / response) | ✅ | Live Call Logs: search, filter by node, export, clear |
| LLM reasoning for routing decisions | 🟡 | edge question + result are logged; no reasoning text |
| Variable inspector (system / custom / extracted, live) | 🟡 | test panel tab "(x) Variables": every value collected in the call, latest per field, with source and time (gap L). No system-variable rows |
| Active node ring, visited nodes dim | 🟡 | active node highlighted; visited nodes are not dimmed |
| ➕ | | minimap, auto-layout, duplicate node, per-node "View logs", Hamsa-JSON import, test cases + publish gate |

### Flow Agent gap list (proposed order)

- ~~**J. Condition operators** — `ne`, `gt / gte / lt / lte`, `contains / not_contains`, `regex` (plus `exists` as an alias of `filled`) in `_holds`, the transition inspector and the Hamsa importer. Small-medium; backend + tests + UI.~~ done
- ~~**K. Static conversation message + richer End node** — a conversation node mode "say exactly this" (per language), and `say` on End nodes rendered as a template (`{{ var }}`), optional prompt-written goodbye. Small-medium.~~ done, except the prompt-written goodbye
- ~~**L. Validation badge + variable inspector** — an endpoint that validates the open graph, a header badge on the canvas (errors / warnings, "Focus" selects the node), warnings for missing fallback edges / empty prompts / unreachable nodes, and a live variables panel in the test panel. Medium, mostly UI.~~ done (client-side validation, no endpoint needed)
- ~~**M. DTMF in flows** — key transitions on conversation nodes, digit capture into a variable (1-20 digits, # / * end, timeout), global key triggers. Medium-large; touches `voice/call.py` and the IVR protocol.~~ done
- ~~**N. Transfer node options** — destination per node (E.164 check), cold / warm with a message, timeout, SIP headers.~~ done
- ~~**O. Tool node behaviour** — on-error continue / retry / fail, timeout, custom spoken response, processing message.~~ done
- ~~**Q. Global node options** — return to source, double confirmation, skip response.~~ done
- ~~**P. Change Agent Settings node + per-node overrides**~~ done (expressiveness / dictionaries wait for H)
- ~~**R. Transfer Agent node**~~ done
- **P / R. Later:** Change Agent Settings node + per-node overrides (voice, prompt, call settings, LLM), Transfer Agent node (agent-to-agent handoff). Larger designs.
- Web tool node: built, see "Web tools".

Verdict: core graph (conversation / router / tool / set / transfer / end, prompt + equation + always transitions, global edges, test call, logs, variable extraction) ✅. Missing: DTMF in flows, static messages, richer operators, per-node settings, validation badge and variable inspector, agent-to-agent transfer.

### 2.4 Variable System  (reviewed and implemented 2026-10-06)

Hamsa pages: `variables/introduction`, `system-variables`, `custom-variables`, `extracted-variables`, `static-variables`,
`availability-and-context`, `syntax-and-naming`, `ui-and-validation`, `advanced`, `api-reference`, `troubleshooting`.
Ours: `runtime/harness/variables.py` (names, system list, custom variables, params) · `runtime/skills/loader.py::template_vars` ·
`runtime/skills/flow.py` (templates, JSONPath) · `runtime/skills/graph.py` (name checks) · `console/src/studio/variables.ts`,
`VariablePicker.tsx`, `validate.ts`, `FlowCanvas.tsx` (Variables panel) · `GlobalSettings.tsx` (Variables section).

| Hamsa component | Status | Ours / note |
|---|---|---|
| System variables (time ×8, call ×4, user ×2, agent ×3) | ✅ | all 17 + `call_lang`, listed from one table (`SYSTEM_VARIABLES`) and shown in the canvas (x) panel; `current_time` is HH:MM; `current_date` is ISO (Hamsa: locale format). Old names `userNumber`, `callParams`, `current_datetime` kept |
| Custom variables (workflow level, defaults, any type) | ✅ | agent-level `variables` in the release: name, type (string / number / boolean / array / object), default, description; edited in Global Settings → Variables; available in every prompt, message, tool argument and global node |
| Custom variables as `params` when a call starts | ✅ | `params` on the start message of the browser call (`/ws`) and the text chat (`/ws/chat`): read against the declared types (text → number / boolean / JSON), undeclared valid names accepted, system names and bad names refused with an error event; carried into an agent-to-agent hand-off. Phone (IVR) calls can't pass params yet — no field in the IVR protocol |
| Extracted variables: AI extraction with type, description, enum | ✅ | conversation `extract` + declared `variables` (string, integer, number, boolean, date, **array, object** added) |
| Extracted: required flag | ❌ | not modelled (a value is optional; the transition conditions decide) |
| Extracted: tool JSONPath (`$.data.items[0].name`, `$`) | ✅ | tool `outputs` accept `$.` JSONPath as well as `result.…`; the Hamsa importer converts them |
| Extracted: DTMF capture | ✅ | conversation `dtmf_capture` (gap M) |
| Static variables (set values, templates, five types) | ✅ | `set` node (JSON values; `"=literal"`, `{{ }}` templates) |
| Syntax `{{ name }}`, Jinja conditionals, filters, expressions, nested objects / arrays | ✅ | sandboxed Jinja; plus Hamsa's `{{ name \|\| 'fallback' }}` (the fallback also applies when the value is empty) |
| Naming rules: snake_case, 1-50, starts with a lowercase letter, no system names | ✅ | enforced for declared / set / tool-output / keypad variables (server: graph error; studio: error badge, add forms refuse) and for custom variables (bundle validation) |
| Availability: system + custom everywhere; extracted / static only in steps that run after the creating step; never in global nodes | 🟡 | at run time every value is in the call's state (no per-step hiding). The studio checks it: a template using a variable that no earlier step collects warns "only collected after this step / on another path", and a transition from Anywhere that depends on a collected variable warns |
| Variables panel (system, custom, extracted summary; add / edit / delete) | ✅ | canvas (x) panel: system (grouped, searchable), custom, call facts, set by tools, collected from the caller (typed, with name validation) |
| "Insert variable" selector filtered by availability, categories, search | ✅ | `(x) Insert variable` above node prompts, static messages and the system prompt: System · Custom · Collected before this step |
| Real-time linting, typo suggestions, type-aware properties, live preview with test values | ❌ | only the availability / naming checks above. No template preview |
| Circular reference detection, usage tracking (unused variables) | ❌ | not built |
| Nested object / array schemas (`properties`, `item`) on extracted variables | ❌ | arrays / objects extract as free JSON; no declared inner schema |

Verdict: system, custom (with params), naming, fallback syntax, JSONPath and the panel / picker ✅; availability by execution order 🟡 (checked in the studio, not enforced at run time); linting preview, usage tracking, nested schemas, required flag ❌.

### 2.5 Publishing  (reviewed and implemented 2026-10-06)

Hamsa pages: `publishing/publish-agent`, `publishing/embed`, plus Hamsa's own Publish screen (looked at live).
**What Hamsa means by "Publish":** a *public page* anyone can open and talk to without signing in, and an *embeddable widget*
(`embed.js`) that opens that page in an overlay on a website. It is not the draft → live release step: ours keeps that as
**Publish** in the Studio (release, gate, versions, rollback — beyond what Hamsa's docs describe), and the public page is the
new **Share** button.

Ours: `runtime/platform/share.py` (settings, limiter) · `runtime/control/share_api.py` (console API) · `runtime/server/public.py`
+ `public_page.py` (public routes, page, embed script) · `db/schema.sql` (`agent_shares`) · `console/src/pages/Share.tsx`.

| Hamsa component | Status | Ours / note |
|---|---|---|
| Release management: draft vs live, versions, history, rollback, change note, gate | ➕ | unchanged (Studio → Publish, Versions tab, test-case gate with audited override). Hamsa's docs don't describe it |
| Public page for an agent, no sign-in, one link per agent | ✅ | `GET /p/{token}`; the link only ever starts the agent's **published release** (never the draft, never another agent) |
| Publish / Unpublish, effective at once | ✅ | Share screen: Publish link / Update link / Unpublish (confirm). Unpublish deletes the token: page and config return 404, new sockets are refused, calls in progress on the link are ended; re-publishing makes a new token |
| Description (60), short line under the title (60), show agent name, show transcript | ✅ | same fields; the transcript overlay shows the agent's spoken lines |
| Theme light / dark, visitor theme switcher (remembered per device) | ✅ | |
| Audio visualizer: Orb / Wave / Aura, preview states (ready, connecting, listening, thinking, speaking), gradient presets (Ocean, Violet, Emerald, Sunset, Amber, Midnight, Rose, Slate) + custom colours | ✅ | canvas visualizers driven by the real audio level (speaker when the agent talks, microphone when listening); "Test with microphone" in the preview is not built; no contrast checker |
| Advanced appearance: page background per mode | ✅ | dark and light background colours |
| Not in Hamsa's list but on its page: logo, "Powered by" badge | 🟡 | no logo upload; our small footer line says "Voice AI by Cloud Solutions" |
| Live preview next to the form | ✅ | the real public page in preview mode inside an iframe, told the settings by `postMessage` |
| Embed widget `embed.js`: position, size, button label, colour, auto-start, launcher (auto / always / never), custom trigger elements | ✅ | `GET /embed.js` (shadow-DOM button + full-screen overlay with the page in an iframe); options come from the Share settings and may be overridden with `data-position`, `data-size`, `data-color`, `data-label`, `data-auto-start`, `data-launcher`; any element with `data-agent-trigger` opens it; Esc / × closes (ends the call) |
| Origin whitelist (not in Hamsa's docs) | ➕ | "Websites that may embed the page": the page's `frame-ancestors` header lists them; the voice socket only accepts the page's own origin |
| Cost protection (not in Hamsa's docs) | ➕ | per link: longest call (default 5 min), calls at the same time (5), calls per visitor per hour (10); in memory per process; `PUBLIC_TRUST_PROXY` reads the visitor address from `X-Forwarded-For` behind a proxy |
| `params` from the host page | 🟡 | only the custom variable names the owner lists ("allowed params") can be set from the link's query string (`?customer_name=Sara`) |
| Web tools (client-side JS) | ✅ | built: `registerTools` in the embed widget and the public page; see "Web tools" below |
| Phone number assignment | ✅ | `/numbers`, routes per agent |

Run time: `WS /ws/public/{token}` uses the same audio protocol as the browser call; calls are `pub-…` ids (shown as web calls) and
appear in Call history / Live calls like any other call; the agent's call limits and the link's own cap both apply.

Verdict: the public page, appearance, embed widget, unpublish, limits and web tools are ✅; logo, "test with microphone" in the preview ❌.

## 3. Tools  (reviewed and implemented 2026-10-06)

Hamsa pages: `tools/introduction`, `tools/function-tools`, `tools/mcp-tools`, `tools/web-tools`.
Ours: `runtime/platform/toollib.py` (policy fields + validation) · `runtime/tools/{types,catalog,executor,http_tool}.py` ·
`runtime/control/tools_api.py` · `console/src/pages/Tools.tsx` (the tool library).

| Hamsa component | Status | Ours / note |
|---|---|---|
| Function tools (API request): name, description, URL, method, parameters as JSON schema | ✅ | Tool library → Add → API request; `{arg}` placeholders in the URL, other args in query / body |
| Authentication: None, Bearer, **Token**, Basic, custom headers | ✅ | added `token` (`Authorization: Token <v>`); bearer / basic / api_key / free headers existed; secrets are Fernet-encrypted `{secret: NAME}` references |
| **Async** toggle (fire and forget) | ✅ | policy `async: true` (write / send tools): the agent gets `{success, queued}` at once, the call runs in the background (TOOLS_MODE respected), the outcome is only logged (`tool.end` / `tool.error` with `background`) |
| Timeout | ✅ | seconds, 1-60 (Hamsa: ms) |
| MCP tools: server URL + auth, tool discovery | ✅ | MCP servers pane (status, discover, per-tool policy); also local (Python) tools |
| Web tools (client-side JavaScript through the SDK, not on phone calls) | ✅ | see "Web tools" below |
| Collections / folders | ✅ | collections in the library (sidebar list, move, new) |
| Versioning with persistent IDs; agents follow the latest | 🟡 | agents run a frozen copy in each release; saving a tool publishes a new release of every agent that has it (new calls use it, calls in progress keep theirs) |
| **Lifecycle messages** "Request start / Request complete" | ✅ | policy `say_start` / `say_done` as `{ar, en}`, templated (`{{args.x}}`); the start line replaces the generic "one moment", the done line is said on success only |
| Global search | ✅ | |
| **Status filter Active / Inactive / All** | ✅ | policy `enabled: false` = inactive: not offered to the model, executor refuses, a flow's tool node takes its failure edge; filter + "Inactive" badge in the library (the old "Active" badge for tools used by agents is now "In use") |
| Draft in the browser while editing | ✅ | drafts in localStorage (secret values never stored) |
| Platform policy (not in Hamsa): kind, confirmation (affirm / readback), claim backing, roles, hooks, cache, idempotency | ➕ | unchanged |

Verdict: everything in Hamsa's Function and MCP tool pages is ✅, and web tools are ✅ (below).

## 4. Knowledge Base  (reviewed and implemented 2026-10-07)

Hamsa pages: `agents/knowledge-base/` introduction, quick-start, creating-items (text / file / URL), managing-items,
status-lifecycle, best-practices.
Ours: `runtime/platform/knowledge.py` (extract, fetch, chunk, embed, search) · `runtime/control/knowledge_api.py` ·
`console/src/pages/Knowledge.tsx` · tables `kb_items`, `kb_chunks` (pgvector).

| Hamsa component | Status | Ours / note |
|---|---|---|
| Text items (name 1-100, content 50-5,000 chars), instant | ✅ | name up to 200, content up to 25,000 chars (more room, no minimum) |
| File items: PDF, DOCX, DOC, TXT, HTML, EPUB, 21 MB | ✅ | PDF, DOCX, TXT, **MD**, HTML, EPUB, 21 MB; old `.doc` is refused with the reason (save as .docx / PDF); the file itself is not kept, only its text |
| **URL items**: https only, sitemap discovery up to 100 pages, no media, no login pages | ✅ | new: `POST /api/knowledge/url/discover` + `/url`; "Add Link" dialog with "Find pages" (sitemap list, search, pick, max 100); pages fetched in the background; a page that can't be read gives the reason (refused / login / 404 / JavaScript-only); a multi-page item with some failed pages is "completed with errors" and says which |
| Fetch safety (not in Hamsa's docs) | ➕ | only public https hosts: every redirect hop is re-checked, private / loopback addresses refused (no reading our own network), 5 MB per page, user-name links refused |
| Content fetched once; refresh = delete and re-add | ✅ | same |
| Statuses PROCESSING / PROCESSED / FAILED / COMPLETED_WITH_ERRORS | 🟡 | processing / completed / completed with errors / failed with the error text; no separate UPLOAD / READING / INGESTION failure states (we read the file during the upload request and refuse it there) |
| Storage quota per plan | ✅ | one quota per project (300 MB), usage bar, uploads refused when full |
| Rename (title only, content not editable) | ✅ | new: "Rename…" row action, `PATCH /api/knowledge/{id}` |
| Delete (typed confirmation), blocked while an agent uses the item | ✅ | now blocked (409) while a published release or a draft uses it; the dialog names the agents; we confirm with a dialog, not by typing DELETE |
| Reprocess | ➕ | "Process again" re-chunks and re-embeds (not in Hamsa) |
| Search + filters: name, type, status, extension, used / unused; sort by date, words, size | ✅ | type filter now has URL |
| Bulk actions | ✅ | none, as Hamsa |
| Assign items to agents, usage per item | ✅ | "Use in agent…" edits the agent's draft (live after Publish); Used / In draft columns |
| Retrieval | ➕ | hybrid vector + keyword search fused by rank, keyword-only fallback when embeddings are down; "try a search" dialog |
| Drafts of the create forms kept in the browser | ❌ | not built (low value) |

Verdict: URL items, rename and delete protection were the gaps; all ✅ now. Not built: form drafts in the browser, separate
upload / reading / ingestion failure states.

## 5. Batch Calls  (reviewed and implemented 2026-10-07)

Hamsa pages: `agents/batch-calls/` introduction, creating-batch-calls, managing-batch-calls, recipients-status, best-practices.
Ours: `runtime/platform/batch.py` (CSV, schedule, dialer) · `runtime/control/batch_api.py` · `runtime/server/ivr.py` (outbound
connect) · tables `outbound_numbers`, `batch_calls`, `batch_recipients` · console `pages/BatchCalls.tsx`, `pages/BatchCall.tsx`,
outbound numbers on `pages/Numbers.tsx`. Protocol: docs/ivr_protocol.md section 9.

**Decision (2026-10-07, with the owner):** the platform cannot place calls itself, so each outbound number has a **dial URL**; the
IVR / PBX places the call and connects the answered call to our IVR socket. `BATCH_LIVE_DIAL=false` (default) simulates everything.

| Hamsa component | Status | Ours / note |
|---|---|---|
| Name 1-100, From number (outbound), Voice agent; number and agent locked after creation | ✅ | from-number = an "outbound number" (Phone numbers page); only published agents; calls run the published version |
| CSV upload: `.csv`, 50 MB, first row = headers, template download | ✅ | `phoneNumber` / `phone_number` required; `name`; `ignoreE164Validation`; template button; 10,000 recipients per batch (ours, keeps the screens responsive) |
| Phone validation (digits, +, ≥ 7 digits, E.164 unless ignored) | ✅ | same rules, per-row error text |
| Extra columns = dynamic variables, silently ignored when the agent doesn't use them | ✅ | `call_params`: invalid names / values that don't fit a declared type are dropped; the rest become call params; `name` too |
| Validation dialog: accepted / rejected counts, inline fixing, duplicates allowed with a warning | ✅ | counts, per-row reasons, edit the phone or remove the row, "Check again"; duplicate count shown |
| Add more CSVs later, view / remove recipients | ✅ | "Add recipients" while unfinished; Remove per row (not while in progress) |
| Send now / Schedule (date, time, timezone) | ✅ | |
| Daily start / end time and allowed days; calls outside are deferred, not skipped | ✅ | window in the batch's timezone, also past midnight; default Sun-Thu 09:00-18:00 Riyadh |
| Concurrency by plan, never fails or pauses because of it | ✅ | `BATCH_MAX_CONCURRENT` (5) shared by all batches, oldest batch first |
| Statuses SCHEDULED → RUNNING → COMPLETED / FAILED; PAUSED; CANCELLED | ✅ | FAILED = nobody was reached; no separate PENDING batch status |
| Pause / Resume / Cancel / Retry (failed + no-answer only, same batch id) / Rename / Delete, each confirmed | ✅ | |
| Recipient statuses PENDING / QUEUED / IN_PROGRESS / COMPLETED / FAILED / NO_ANSWER | 🟡 | no QUEUED (a call goes straight to in progress when a slot is free) |
| List: name, recipients, status, search; detail: summary, progress, configuration; reload | ✅ | list adds agent, number, progress bar, status filter; auto-refresh while running |
| Recipient list: filter by status, search, variables; call details for completed / failed | ✅ | duration + link to Call history (`/calls/<id>`), failure reason |
| Dial URL, the way calls are really placed (not in Hamsa) | ➕ | webhook + `outbound_token` + status callback; ring timeout → no-answer; a connected call that never ends is failed after 2 h; cluster-wide lock so several workers never place a call twice |

Verdict: ✅ for everything Hamsa documents except the QUEUED status. Not verified against a real PBX (none is connected): the dial request
and callback are covered by tests with a fake dial URL and a fake IVR socket only.
Not built: per-batch concurrency, call retries with delays, "answering machine" handling, per-recipient call detail inside the batch page.

## 6. Call History  (reviewed and implemented 2026-10-07)

Hamsa pages: `agents/call-history/` introduction, viewing-calls, call-details, best-practices (the last is an empty placeholder).
Ours: `runtime/control/api.py` (`/api/calls`, `/api/calls/{id}`, `/instruction`) · `runtime/control/store.py` (`list_calls`) ·
`console/src/pages/Calls.tsx` (list) · `pages/CallPanel.tsx` (details drawer) · `pages/CallDetail.tsx` (timeline, waterfall).

| Hamsa component | Status | Ours / note |
|---|---|---|
| Columns Time, Agent, Channel, Duration, Status (visible); Agent Number, Client Number, Timestamp (hidden); column chooser | 🟡 | Time, Agent, User Number, Channel, Duration, Status, Outcome visible; Call ID, Language, Turns, Latency p50, Agent version optional; chooser **remembers** the choice (Hamsa resets it). No "Agent Number" column (the number called isn't stored on the call row) |
| Cost column (credits) | ❌ | no pricing model in the platform, so no cost per call |
| Date range presets All / Last hour / Today / Yesterday / This week / This month / Custom | ✅ | |
| Status filter (multi) | 🟡 | In progress, Completed, Failed, Forwarded, Terminated; no Pending / No answer — unanswered batch calls live on the batch call's recipient list, not here |
| Duration filter (between, greater than, less than, equal to; seconds) | ✅ | new: toolbar "Duration" popover, `dur=between:10:60` in the URL, `/api/calls?duration_op=…&duration_a=…&duration_b=…` |
| Channel filter (Web / Telephone) | ✅ | plus Chat Agent |
| Agent filter | ✅ | |
| Search by call id, user number, agent id | ✅ | numbers match however typed (0548…, +966 54…, last digits) |
| Sort by time and duration (Hamsa also cost); default newest first | ✅ | |
| Pagination, page size | 🟡 | 25 per page, previous / next; no page-size selector |
| **Export CSV**: current page / all filtered results | ✅ | new: "Export CSV" menu; the columns you have turned on; UTF-8 with BOM (Arabic opens correctly in Excel); formulas neutralised; all-results stops at 10,000 calls and says so |
| Live status and duration updates | ✅ | list refreshes every 15 s, open call every 3 s |
| Filters, sort, page in the URL | ✅ | |
| Details drawer: Overview / Conversation / Logs / Outcome | ✅ | same four tabs; plus latency waterfall and the agent release / models used |
| Conversation: transcript with speaker + time, search, copy | ✅ | speaker filter too |
| Live monitoring: listen to an active call (mute / leave) | ✅ | "Listen in" card on running calls, both sides, per-side mute — section 15 |
| **Real-time instructions** to the agent during a call | ✅ | new: Overview of a running call → "Send instruction to agent…"; the text joins the agent's prompt as a supervisor note for the rest of the call (last five count, never read out), is logged on the call (`supervisor_instruction`), and reaches the right worker in a cluster. Voice / IVR calls; not text-chat tests |
| Outcome tab: results, parameters, success indicators | ✅ | outcome flags + every variable the agent collected + the AI "Call analysis" card (summary, sentiment, CSAT / NPS estimates, your outcome fields) |
| Recording | ✅ | per-agent "Record calls" (off by default): stereo WAV, encrypted, local folder, 30 days, player in the call panel. See "Call recordings" below |
| Pending calls can't open the drawer | ✅ | n/a — we have no pending calls |

Verdict: duration filter, CSV export and real-time instructions were the gaps and are ✅. Open: cost per call, agent-number column,
recordings, page-size selector.

## 7. Phone Numbers  (reviewed and implemented 2026-10-07)

Hamsa pages: `agents/phone-numbers/` introduction, quick-start, managing-numbers, making-calls, best-practices (an empty
placeholder), API `call-phone-number`; Telephony Dashboard (`agents/telephony/introduction`, only a short description).
Ours: `runtime/control/agents_api.py` (`/api/routes`) · `runtime/control/batch_api.py` (`/api/outbound-numbers`,
`/api/outbound-calls`) · table `phone_routes` (+ `label`, `created_at`) · `console/src/pages/Numbers.tsx`.

| Hamsa component | Status | Ours / note |
|---|---|---|
| Number registry: E.164 number, label, created time | ✅ | routed numbers now have a label (≤ 100) and a created date; exact numbers, IVR extension prefixes (`8880*`) and `*` (default) |
| Providers Twilio and SIP trunk (credentials, Origination URI, destination, transport, headers, connection test) | ➖ | not applicable: the platform has no telephony of its own — the customer's IVR / PBX is the provider and connects calls to our IVR socket (docs/ivr_protocol.md). The outbound side is the **dial URL** (+ optional bearer token kept as a secret) |
| Assign a number to one agent; reassign with a warning; unassign | ✅ | agent dropdown per number; changing it asks for confirmation; "Unassign" asks too and says where calls go instead (the default agent). A number belongs to one agent and one project |
| Edit | ✅ | label, agent; outbound numbers: dial URL and token |
| Delete (permanent, confirmed) | ✅ | confirmation dialogs for routes and outbound numbers; the default route can only be re-pointed; an outbound number used by an unfinished batch can't be removed |
| Status Assigned / Unassigned / None | 🟡 | a number in the list is always assigned; unassigned = removed |
| List, search, filter, bulk actions | 🟡 | none of the three exist in Hamsa's docs either; our list is short |
| **Make Outbound Call** (button; destination E.164, source number, agent, custom parameters) | ✅ | new: "Make outbound call" on every outbound number → number to call, agent (published, defaults to the agent that answers that number), parameters; creates a one-recipient batch call that starts at once and shows in Batch calls and Call history |
| API: Create Outbound Call (`voiceAgentId`, `phoneNumber`, `toNumber`, `params`) | ✅ | `POST /api/outbound-calls {agent_id, from_number, to_number, params}` (console sign-in token); the response carries the batch call id |
| Parameters reach the agent; unused ones ignored without error | ✅ | same as batch calls (`call_params`) |
| Limits: concurrent calls by plan, E.164 validation, errors | ✅ | `BATCH_MAX_CONCURRENT`, E.164 check up front with the reason, dial errors on the recipient |
| Telephony Dashboard (Hamsa: numbers, assign, test outbound call, SIP trunks) | ✅ | the Phone numbers page + Batch calls |

Verdict: label / created date, confirmed reassign / unassign / delete, and the single outbound call (screen + API) were the gaps and are ✅.
Not built: API keys for calling the API from outside the console (sidebar "API keys — soon"), a connection test for the dial URL, number purchase.

## 12. Webhooks  (reviewed and implemented 2026-10-07)

Hamsa pages: `agents/webhooks/introduction` (dashboard setup), `developers/agent-guides/webhook-integration-guide` (events, payload,
handler advice). Outcomes data comes from the dashboard's Satisfaction / Outcome pages (post-call analysis).
Ours: `runtime/platform/webhook.py` (`WebhookSink`) · `runtime/platform/bundle.py` (validation) · `runtime/control/agents_api.py`
(`/webhook/test`, `/webhook/deliveries`) · table `webhook_deliveries` · `console/src/studio/GlobalSettings.tsx` (Call Webhook).

| Hamsa component | Status | Ours / note |
|---|---|---|
| One webhook per agent: URL (HTTPS only, valid certificate) | ✅ | `bundle.webhook.url`; http:// and local addresses are refused when the draft is saved; applies after Save + Publish; the release a call started on is used for the whole call |
| Authentication: none / Bearer token (`Authorization: Bearer …`) | ✅ | token stored as an encrypted secret (`WEBHOOK_TOKEN_<AGENT>`), only its name is in the release |
| Events `call.started`, `call.answered`, `transcription.update`, `tool.executed`, `call.ended` | ✅ | all five, each switchable; sent in the order they happened per call. `call.started` and `call.answered` both fire when the call connects (we have no ringing phase) |
| Envelope `eventType, callId, timestamp, projectId, agentId, agentName, data: {timestamp, data}` | ✅ | same shape |
| `call.ended` data: `conversationId`, `conversationRecording`, `transcription: [{Agent}, {User}]`, `outcomeResult` | ✅ | transcription ✅ (masked, can be left out); `conversationRecording` is still always null (recordings are only played in the console); `outcomeResult` = echoed custom parameters + the agent's outcome fields from post-call analysis, plus an `analysis` object; `call.ended` waits up to 35 s for it |
| Custom parameters echoed back so the receiver can match its own records | ✅ | the `params` a call started with (console call, batch CSV columns, outbound call parameters, public-link params) come back in `call.ended.outcomeResult` and in `call.started` / `call.answered`; read from the live call, never stored in the event log |
| `tool.executed`: tool name, input, output | 🟡 | `toolName`, `input`, `success`, `duration`; `output` is null (tool results aren't kept in the event stream, they may hold patient data) |
| Receiver rules: always answer 200 within ~4 s, retry with backoff, de-duplicate with `callId:eventType:timestamp` | ✅ | 5 s timeout; 3 attempts (waits 2 s, 4 s) on network errors, 429 and 5xx; other 4xx are not retried; a failure becomes a WARNING in Logs and never touches the call. New: `X-Webhook-Event` and `X-Webhook-Id` headers — one id per event, the same on every retry — for de-duplication |
| Request signing (Hamsa: none) | ➕ | optional signing secret: `X-Webhook-Timestamp` + `X-Webhook-Signature: sha256=` HMAC-SHA256 of `timestamp.body` |
| Test delivery | ✅ | "Send test call.ended" sends a sample with the token / signing secret you have just typed (no need to save first) and shows what the receiver answered |
| Delivery monitoring (not in Hamsa's docs) | ➕ | "Recent deliveries" in the section: event, call, delivered / failed, what the receiver answered, attempts, time; the latest 200 per agent are kept; tests appear there too |
| Never sends the caller's number | ➕ | everything comes from the PII-masked event stream |
| Project-level webhook (one URL for all agents) | ❌ | Hamsa's docs only describe a per-agent webhook; not built |

Verdict: ✅ for everything the two Hamsa pages describe; 🟡 only where we have no data (recording, tool output).
Not built: a project-level URL, a "resend" button. Not run against a real
receiver (the URL must be HTTPS); delivery, retries, ids and signatures are covered by tests with a fake receiver.

## 13. Testing Dashboard  (reviewed and implemented 2026-10-07)

Hamsa pages: `agents/testing/introduction` (browser test, phone test, live transcript, flow debugging, variable monitoring, live
logs), `developers/guides/testing` (automated testing, CI/CD — marked "Coming soon"), API `test-api-tool`. The Telephony Dashboard page
describes nothing beyond what sections 11 / 9 already cover.
Ours: `console/src/pages/Studio.tsx` (Test menu) · `studio/TestPanel.tsx`, `useTestCall.ts` · `studio/TestsTab.tsx` ·
`console/src/pages/Numbers.tsx` (`MakeCall`) · `runtime/control/batch_api.py` · `runtime/server/ivr.py`.

| Hamsa component | Status | Ours / note |
|---|---|---|
| **Test Agent** (browser call) | ✅ | Studio → Test; microphone call or chat; tests the draft by default ("Test the draft", off = the published version) |
| **Test via Phone** (a real phone call) | ✅ | new: Test ▾ → "Test via phone…": your number, an outbound number, parameters → the agent's **draft** calls you (nothing needs to be published). Needs an outbound number and a real PBX behind its dial URL (`BATCH_LIVE_DIAL=true`); until then it is simulated. The call shows in Batch calls ("Test call to …") and Call history |
| Real-time transcript | ✅ | test panel Chat tab, with the agent's lines as they are spoken |
| Conversation flow debugging | ✅ | "Follow canvas" highlights the node the call is in; click a log line to locate its node |
| Variable extraction monitoring | ✅ | test panel "(x) Variables" tab: every collected value, latest per field, source and time |
| Live logging | ✅ | Logs tab with level filters (error / warning / info / debug), per-turn latency, event details |
| Test an API tool configuration (retries, streamed attempts) | 🟡 | Tools → test dialog calls the tool (reads only, one request, shows status / body / time); no streamed attempts |
| Automated testing, CI/CD (Hamsa: "coming soon") | ➕ | ahead: per-agent **test cases** (scripted lines or an LLM-simulated caller with goals / facts, checks for tools, booking, language, gender, no medical advice, length) in the Tests tab; the **publish gate** needs them to pass on the exact draft (or an audited override); `python -m evals` for CI |
| Test with a draft vs published | ➕ | draft by default in every test type |

Verdict: the phone test was the only gap and is ✅; the rest of what Hamsa documents existed, and automated testing is beyond Hamsa's.
Not built: streamed tool-test attempts. The phone test is untested against a real PBX (none connected): covered by tests with a fake IVR socket (the draft is what loads).

---
Review complete (2026-10-07): Dashboard → Agents → Tools → Knowledge Base → Batch Calls → Call History → Phone Numbers → Webhooks → Testing
Dashboard (Telephony Dashboard folded into Phone Numbers / Batch Calls). Open items across modules are listed in each section's verdict.

## 14. Post-call analysis  (dashboard gap B; implemented 2026-10-07)

Hamsa: Global Settings → **Outcome** ("post-conversation processing prompts"), the dashboard's **Satisfaction & Outcome** tab (CSAT, NPS,
sentiment, FCR, escalation) and `GET agent-analytics/satisfaction`, and `outcomeResult` in the `call.ended` webhook. Hamsa's CSAT / NPS
come from per-call survey answers; sentiment needs enabling per agent.
Ours: `runtime/platform/analysis.py` (prompt, validation, `AnalysisSink`, numbers) · table `call_analysis` · `/api/calls/{id}/analyze`,
`/api/analytics/satisfaction` · Global Settings → Outcome · Call History → Outcome tab · Dashboard → Satisfaction & Outcome.

**Decision:** we have no survey, so after each call the agent's own model reads the (already PII-masked) transcript once and *estimates*
the satisfaction numbers. They are labelled "estimated" in every place they appear.

| Hamsa component | Status | Ours / note |
|---|---|---|
| Outcome: per-agent post-call processing | ✅ | `bundle.analysis {enabled, summary, sentiment, satisfaction, fields}`; off by default; applies after Save + Publish |
| Your own outcome fields (structured result) | ✅ | up to 20 fields (snake_case name, string / number / boolean / enum + options, description) filled from the conversation, or left empty; every value is checked against its type and dropped when it doesn't fit; returned as `outcomeResult` in the webhook and shown in the call drawer |
| Summary | ✅ | 2-3 sentences in the language of the call |
| Sentiment (positive / neutral / negative) | ✅ | per call; split shown on the dashboard |
| CSAT | 🟡 | **estimated** 1-5 per call; dashboard score = share of calls rated 4-5 (Hamsa's definition) with Hamsa's bands (> 85 excellent … < 65 investigate); the model may answer "can't tell" (null) |
| NPS | 🟡 | **estimated** 0-10 per call; dashboard NPS = promoters (9-10) minus detractors (0-6), Hamsa's bands |
| First-call resolution, escalation rate | ✅ | already from the call data (no AI); plus new "resolved by the agent" (the model's judgement) |
| Satisfaction & Outcome tab with real values; shows how many calls they rest on | ✅ | "Caller Satisfaction (estimated)" block: CSAT, NPS, resolved, sentiment, with analysed / too short / failed counts |
| API: satisfaction analytics | ✅ | `GET /api/analytics/satisfaction?start&end&agent` → analysed, csat {score, average, calls}, nps, sentiment, resolved (our field names, console sign-in) |
| `call.ended` webhook `outcomeResult` | ✅ | custom parameters + the outcome fields; plus `analysis` {summary, sentiment, csat, nps, resolved}. `call.ended` waits up to 35 s for the analysis |
| Call drawer Outcome: summary, sentiment, resolution | ✅ | "Call analysis" card with Analyze / Analyze again (works on any ended call, also before it was turned on) |
| Short calls | ➕ | fewer than 2 caller turns are recorded as "skipped" with the reason (not analysed, not counted) |
| Privacy | ➕ | only the masked transcript goes to the model — the same model that already handles the call; no phone numbers or ids |

Verdict: summary, sentiment, outcome fields, the webhook data and the drawer are ✅; CSAT and NPS are 🟡 by design (estimates, not surveys).
Not built: a real post-call survey (SMS / IVR question) for true CSAT / NPS, a Sentiment column in Call history, per-field prompts,
a separate model choice for analysis (it uses the agent's own model), analysis of text-chat tests.

## 15. Live listening  (Dashboard gap D and Call History "live monitoring"; implemented 2026-10-07)

Hamsa: Live Calls → open a call → **Start Live Monitoring** (join as a listener, mute / leave), with the real-time instruction box.
Ours: `runtime/voice/call.py` (`add_listener`, `_tap`) · `runtime/control/api.py` (`WS /api/live/listen`) · `console/src/listen.tsx`
(`ListenCard`) · used in `pages/CallPanel.tsx` (Call History and Live calls → Open call) and `pages/LiveDrawer.tsx` (dashboard Live Calls).

| Hamsa component | Status | Ours / note |
|---|---|---|
| Start / stop listening to an active call | ✅ | "Listen in" card on every running call: Start listening / Stop; plays in the browser through Web Audio with a 150 ms buffer |
| Hear both sides | ✅ | the caller (as received, before echo cancellation) and the agent (what it is saying) arrive as two labelled streams, each in its own sample rate |
| Mute / leave | ✅ | Mute the caller and the agent separately; Stop leaves; leaving the page stops it; a "speaking" dot per side |
| Speaking to the caller / joining the call | ➖ | listen-only by design (Hamsa's docs describe a listener and text instructions, not a voice join); use the instruction box to steer the agent |
| Who may listen | ➕ | owners / admins of the call's project (same sign-in as the console); a call of another project is refused |
| Audit | ➕ | every listener is written to the agent's audit log (`call.listen`, who, call id) |
| Limits | ➕ | up to 3 listeners per call; a slow listener loses audio, the call is never slowed down; nothing is recorded or stored |
| Works for | ✅ | phone (IVR), browser, public-link and outbound calls handled by the server the console is talking to |
| Several server workers | 🟡 | the listener must reach the worker that holds the call; with `WORKERS > 1` a call on another worker answers "not running here" (audio isn't relayed through Postgres). Real-time instructions do work across workers |
| The caller is told | ❌ | no announcement or beep to the caller or the agent — check your own consent rules before using it on real calls |

Verdict: ✅; gap D closed. Not built: relay across workers, joining by voice, an on-screen level meter, recording the listened audio.

### 14.1 Comparison with Hamsa's live Outcome tab  (2026-10-07, looked at in Hamsa's own console: HMG project → Call History → first call → Outcome)

What Hamsa does: there is **no built-in summary**. Global Settings → **Outcome** holds an "Outcome Data Structure" — a list of properties
(Name, Type String / Number / Boolean / Array / Object, Required, Enum, Description = the instruction for the model; an "AI Prompt" button
that writes the schema from a prompt, a Visual / JSON switch). The HMG agent has 14, among them `summary` ("One clear paragraph: who the
caller was, what they wanted, what was done, and where they got stuck"), caller sentiment, call outcome, objective met, primary intent,
doctor, clinic, hospital, appointment date / time. The call's **Outcome tab** shows one card per property (with a copy button; empty ones
too) plus the call's default params. It runs for every call once the schema exists.

| Hamsa | Ours (before) | Now |
|---|---|---|
| Summary is a field you define | built-in summary switch + a "Call analysis" card above the details | unchanged (superset); the card has copy buttons like Hamsa's cards; fields show as cards, empty ones as "—" |
| Outcome runs automatically once the schema is saved | Outcome is part of the **published version**: switched on in the draft but not published → calls show nothing, with no hint | the card says exactly why: *not published yet* (this call ran on version N) with a link to the agent, or *off for this agent*, or *not analysed yet*; **Analyze** now uses the agent's current settings (draft) when the call's version had none |
| Types String, Number, Boolean, Array, Object (+ Enum) | string, number, boolean, enum | + **array**, **object** |
| Required checkbox | — | not needed: a value the call didn't give stays empty |
| "AI Prompt" writes the schema; Visual / JSON switch | — | **+ Suggested fields** (caller name, call reason, objective met, follow-up needed); no prompt generator / JSON view |
| Import of the agent brings the schema | the schema was only listed in the import report | **imported as our Outcome fields and switched on** in the first published version (names made snake_case, enums kept, built-in summary / sentiment off when the schema has its own) |

Why your recent calls showed no summary: the HMG agent's **draft** has Outcome on, the published version (v6, the one calls run on) has none. Publish
the agent and new calls are analysed; **Analyze** gives an existing call its summary now.

## 16. API keys  (implemented 2026-10-07)

Hamsa: dashboard → create an API key (docs `overview/create-api-keys`, `overview/auth`); requests carry `Authorization: Token <api-key>`;
"Get project by API key". Nothing in the docs about listing, revoking, scopes or limits.
Ours: `runtime/control/keys_api.py` · `runtime/control/accounts.py` (`resolve_key`, the `key` principal) · `runtime/control/api.py`
(`bearer_of`, `key_refusal`, `rate_limited`, `require_console`, `use_project`) · table `api_keys` · `console/src/pages/ApiKeys.tsx` (sidebar "API keys").

| Hamsa component | Status | Ours / note |
|---|---|---|
| Create a key in the dashboard | ✅ | API keys page → Create: name, access (full / read only), expiry (never / 30 / 90 days / 1 year); the key (`hmg_…`) is shown **once** with copy buttons and ready-made `curl` examples |
| `Authorization: Token <key>` | ✅ | also `Authorization: Bearer <key>` and `X-API-Key: <key>` |
| Get project by API key | ✅ | `GET /api/whoami` → the project and the key (name, prefix, scope) |
| The key can use the API | ✅ | a key acts as an **admin of its own project** through the same HTTP API as the console (start calls and batch calls, call history, analytics, agents, knowledge, numbers …) |
| List / revoke keys | ✅ | list with prefix, access, created, last used, expires, status; revoke at once (confirmed); max 20 active keys per project |
| Scopes (not in Hamsa's docs) | ➕ | **read only** = GET requests only; **full** = everything below the limits |
| Limits of any key (not in Hamsa's docs) | ➕ | cannot manage keys, people (members / invitations), projects (create / rename) or secrets (write), cannot listen to calls, never sees or touches another project (`X-Project` for another project → 403) |
| Storage | ➕ | only the SHA-256 is stored; a lost key is replaced, not recovered; an invalid, revoked or expired key is refused (401) and never falls back to the open console |
| Rate limit (not in Hamsa's docs) | ➕ | `API_KEY_RATE_PER_MIN` (120) per key and server process → 429 with `Retry-After` |
| Audit | ➕ | creating / revoking a key and every change a key makes are in the project audit log under "API key <name> (hmg_xxxx…)" — never the key |

Verdict: ✅. Not built: per-key allowed endpoints or IP addresses, a rate limit shared across server workers, signing in with a key from the console page itself.
Example: `curl https://<host>/api/outbound-calls -H "Authorization: Token hmg_…" -H "Content-Type: application/json" -d '{"agent_id": "…", "from_number": "+966…", "to_number": "+966…"}'`.

## Web tools (Tools → Web Tool)

Hamsa registers web tools in two places: the **definition** in the dashboard (name, description, parameters, timeout, messages — no URL, no auth) and the **implementation** on the website through its SDK, by the same name. We follow that model; a page can supply implementations but can never add a tool to the agent. MCP servers and API tools are unchanged.

| Hamsa component | Status | Ours / note |
|---|---|---|
| Add New Tool → Web Tool (name, description, timeout, active, start / done messages, parameter schema) | ✅ | `source: web` in the tool library; no URL or auth fields |
| The site registers the function | ✅ | `VoiceAgent.registerTools({name: fn})` (embed widget) or `window.VoiceAgentTools`; the public page asks its host page through `postMessage` |
| Runs in the visitor's browser | ✅ | server → `web_tool` event over the call socket → page runs the function → `web_tool_result` back; result is plain data, up to 20,000 characters; times out at the tool's timeout |
| Not on phone calls | ✅ | the model is only offered a web tool the page registered on this call; a flow tool node for it takes its failure edge |
| Tool in the opening turn | ➕ | the socket keeps reading while the opening runs, so the first turn can use a web tool |
| Origin checks | ➕ | widget only trusts its own iframe and origin; page only trusts its parent |
| Async tools, Active / Inactive, say start / done | ✅ | same as other tools |
| Test button | ➖ | refused with an explanation: a web tool needs a live visitor page (use the demo page) |

Verdict: ✅. Not built: web tools on phone calls (no browser), results over 20 KB, a console "test with a page" button. Docs: `docs/web_tools.md`; demo: `docs/examples/web-tools-demo.html`.

## Call recordings

Decided with the owner: local folder, 30 days, anyone with access to the agent may play.

| Component | Status | Ours / note |
|---|---|---|
| Switch | ✅ | Global Settings → Call settings → Record calls (per agent, off by default) and Keep recordings (days, default 30) |
| Consent | ✅ | the agent says `RECORDING_NOTICE` (editable, ar / en) before anything else |
| Audio | ✅ | stereo 16 kHz WAV, caller left / agent right, on the call's own timeline; phone and web calls |
| Secrets | ➕ | the caller's audio is not kept while a one-time code is being read out |
| Storage | ✅ | `RECORDINGS_DIR` (default `recordings/`, git-ignored), one file per call, encrypted with `MASTER_KEY` (no key: nothing is recorded, a warning is logged); metadata in `call_recordings` |
| Retention | ✅ | deleted automatically when the agent's retention is over (hourly job); status shows "expired" |
| Access | ✅ | anyone who can see the call (project members, API keys with access): play, download, delete in Call History → call panel; never a public link |
| Audit | ➕ | every play, download and deletion is in the agent's audit log |
| Webhook `conversationRecording` | ❌ | still null: no signed download link yet |

Verdict: ✅. Not built: S3 storage, Opus compression (WAV is about 2 MB a minute before encryption), recording of listen-in audio, a download link in the webhook, a per-call "do not record" option.

## Left sidebar: items commented out (2026-10-08)

Hamsa's sidebar has no Skills, Agent models, Connections, Evals or Playground. We hid three of them and kept two:

| Item | Now | Why |
|---|---|---|
| Agent models | commented out | per-agent model setup now lives in Agent Studio -> Global settings |
| Evals | commented out | test cases and the publish gate live in Agent Studio -> Tests |
| Playground | commented out | Agent Studio has its own test panel (browser call / chat) |
| Skills | kept | the only place to edit a skill's text; Agent Studio links to it |
| Connections | kept | the only place for the keys and URLs of the LLM / STT / TTS providers; Global settings links to it |

Only the sidebar entries are commented out (`console/src/App.tsx`, `GROUPS`); the routes `/providers`, `/evals` and `/playground` still work, so
links and bookmarks keep working. To bring one back, uncomment its line.


## Flow nodes: Hamsa docs (docs.tryhamsa.com/agents/flow-agent/nodes) against ours — 2026-10-08

Hamsa has 10 node types. Ours (canvas `TYPES`) covers them; the only differences are small:

| Hamsa node | Ours | Notes |
|---|---|---|
| Start (conversation or tool mode; mandatory) | the flow's `start` node, any type (conversation, or a tool node = tool mode) | Added: the start node can't be deleted (move "start" to another node first); new agents begin with one start node (label "Start Node", prompt "You are a helpful assistant."); play icon |
| Conversation (prompt / static, extract variables, DTMF capture, transitions, global) | conversation | same; Jinja `{{ }}` templates |
| Tool (timeout, onErrorBehavior continue/retry/fail, errorMessage, customResponse, outputMapping, processing message, putOnHold) | tool | Added `error_say` (errorMessage). Not built: put the caller on hold while the tool runs; processing message as an AI-generated line (ours is fixed text) |
| Web Tool (browser tools, web calls only) | a tool node that calls a web-source tool (`source: web`) | same limit: web calls only |
| Router (equation / always; ==, !=, >, <, >=, <=, contains, not_contains, regex, exists, not_exists, all / any) | router | same operators |
| Transfer Call (E.164, warm / cold, message, timeout 1-60 s, SIP headers, global) | transfer | same |
| Transfer Agent (agentId, handoffConversation, handoffVariables, message, timeout, global) | agent | same, except a per-node timeout |
| End Call (final message static or prompt, silent) | end | static message or silent; AI-generated farewell not built |
| Set Local Variables (string / number / boolean / array / object, `{{ }}`) | set | values are expressions / templates; no per-variable type or description field |
| Change Agent Settings (system prompt, voice, expressiveness, STT model, dictionaries, interrupt, response delay, inactivity, min interruption, VAD) | settings | same, except expressiveness and pronunciation dictionaries (we have neither setting) |
| Global nodes (prompt / DTMF trigger, return to source, double confirm, skip response) | "reach from anywhere" edges with `back`, `confirm`, `silent` | same |
| Node `label` and `description` | added: optional `label` (shown on the canvas) and `description` | |
