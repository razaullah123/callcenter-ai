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

## 2026-10-06 — Agents module: review of 2.1 Voice Agents + 2.2 Single Prompt Agent

Review only (no code changes). Read Hamsa's `agents/introduction` and the six `single-prompt/*` pages, compared with
`Agents.tsx`, `studio/GlobalSettings.tsx`, `platform/bundle.py::KNOBS`, `skills/loader.py::template_vars` and
`harness/engine.py` (greeting). Findings and proposed gaps E-I are in `hamsa_parity.md` section 2.

## 2026-10-06 — Agents: gaps E and F

| Component | Gap | Change | Files |
|---|---|---|---|
| System variables for prompts | E | `current_time`, `current_date`, `call_id`, `direction`, `user_number`, `user_number_area_code`, `agent_name`, `agent_number` added next to the old names; `Session` gets `agent_name`, `agent_number`, `direction`; the voice and chat transports set `agent_name` | `runtime/skills/loader.py::template_vars` / `area_code`, `runtime/harness/session.py`, `runtime/voice/call.py`, `runtime/server/chat.py` |
| Dynamic greeting | F | The greeting is rendered as a template when it contains `{{ }}` / `{% %}`; an empty or failed render falls back to the plain text | `runtime/harness/engine.py::Agent.greeting` |
| Wait for user to speak first | F | New per-agent knob `voice_wait_for_user` = never / always / outbound (validated by `CHOICES`); with `always` (or `outbound` on an outbound call) the agent says nothing at start and answers the caller's first words | `runtime/config.py`, `runtime/platform/bundle.py`, `runtime/harness/engine.py::Agent.start`, `console/src/studio/GlobalSettings.tsx` (Call Settings select + greeting help) |

Tests (`tests/test_harness.py`, 6 new): variables and area code, dynamic greeting, plain greeting unchanged, wait always, wait outbound-only, knob validation. Full suite 377 passed; console `npm run build` clean.
Not checked in a browser (the new select renders in Studio -> Global Settings -> Call Settings) or on a live voice call.
Note: `outbound` only matters once we place outbound calls (Batch calls module); `agent_number` stays empty until the IVR / web transports pass the called number.

## 2026-10-06 — Agents module: review of 2.3 Flow Agent

Review only (no code changes). Read Hamsa's flow-agent overview, global settings, global nodes, transitions, DTMF,
debugging, best practices and the ten node pages; compared with `skills/graph.py`, `harness/engine.py` (node
execution), `voice/call.py::on_dtmf`, `studio/FlowCanvas.tsx` and `studio/TestPanel.tsx`. Findings and proposed gaps
J-R are in `hamsa_parity.md` section 2.3.

## 2026-10-06 — Flow Agent: gaps J, K and L

| Component | Gap | Change | Files |
|---|---|---|---|
| Condition operators | J | `ne`, `gt`, `gte`, `lt`, `lte`, `contains`, `not_contains`, `regex`, `exists`, `not_exists` in edge conditions (numbers compared as numbers; a non-number or bad pattern never matches); a bad regex is a graph error; the Hamsa importer maps its operator names (and no longer skips them); transition inspector gets a "compare" mode (variable / operator / value) and the canvas rows show `age > 18` | `runtime/skills/graph.py` (`_holds`, `errors`), `runtime/platform/hamsa_import.py`, `console/src/studio/FlowCanvas.tsx` (`CMP`, `CompareFields`) |
| Static conversation message | K | A conversation node with `say` is said as written when the flow reaches it, with no model call, `{{ }}` filled in; if the caller replies and no transition fires, its prompt (when it has one) takes over, else the line repeats. Node inspector: "Prompt — the agent writes it" / "Static — said exactly". The importer turns Hamsa's static messages into `say` instead of a "say exactly…" prompt | `runtime/harness/engine.py` (`_say_static`, `_flow_line`), `runtime/platform/hamsa_import.py`, `FlowCanvas.tsx` |
| End node message | K | `say` is rendered as a template | `runtime/harness/engine.py` |
| Start node entered marker | K | fix: a flow's first node counted as "already replied" at call start, so a `replied` transition out of a static start node fired immediately | `runtime/skills/graph.py::current` |
| Validation badge | L | live badge + list with Focus on the canvas (errors and warnings, see `hamsa_parity.md`) | `console/src/studio/validate.ts`, `FlowCanvas.tsx` (`ValidationBadge`) |
| Variable inspector | L | test panel tab "(x) Variables": latest value of every collected variable with source and time, live | `console/src/studio/TestPanel.tsx`, `pages/Studio.tsx` |

Tests (`tests/test_graph.py`, 6 new; `tests/test_projects.py` updated for static import): operators, bad regex, numeric router, static message without a model call, static then next node + templated goodbye. Full suite 382 passed (one timing-sensitive IVR test failed once under load and passed on two reruns). Console `npm run build` clean.
Checked in Chrome on a throw-away server (8092) with nothing saved: HMG flow shows "Valid"; a new empty node gives 3 warnings with Focus; Static mode and its message fields; compare transition (`age > 18` on the canvas); chat test fills the Variables tab (`language`, `intent`) while the canvas follows the active node. Not tried: a static message on a real voice call; the End-node template on a live call.
Not done from K: an LLM-written ("prompt") goodbye on End nodes.

## 2026-10-06 — Flow Agent: gaps M, N, O and Q

| Component | Gap | Change | Files |
|---|---|---|---|
| Keypad transitions | M | condition `dtmf: "1"` (key 0-9 * #, validated); a pressed key counts for the turn only (`slots._dtmf`); `Graph.dtmf_keys(node)` = own + global keys | `runtime/skills/graph.py`, `runtime/harness/engine.py` (`dtmf_plan`, `handle`) |
| Keypad digit capture | M | conversation node `dtmf_capture` {variable (snake_case), max_digits 1-20, end_keys # / *, timeout_s 1-30}; the call buffers digits, ends on an end key / the maximum / a pause, stores them in the variable and runs a turn; other keys that a transition listens for move the flow; flows without keypad use keep the old mobile / code entry. The log shows only the digit count | `runtime/voice/call.py` (`on_dtmf`, `_flow_dtmf`, `_dtmf_capture_done`, `_dtmf_turn`, timer), `console/.../FlowCanvas.tsx` (`CaptureFields`, key picker) |
| Transfer node options | N | `destination` (E.164 / extension / template), `transfer_type` warm / cold, announcement `say`, `timeout_s`, SIP `headers`; the transports pass `destination`, `timeout_s`, `headers` on (`transfer(reason, **options)`); IVR sends them in its transfer action; doc updated | `runtime/harness/engine.py` (`_handoff`, `_transfer_options`), `voice/player.py`, `server/chat.py`, `server/ivr.py`, `docs/ivr_protocol.md`, `FlowCanvas.tsx` (`TransferOptions`) |
| Tool node behaviour | O | `on_error` continue / retry / fail, `retries`, `timeout_s`, `processing` line, `say` after success; `fail` hands the call to a person | `runtime/harness/engine.py` (`_run_tool_node`, `_execute_with_filler(filler=)`), `FlowCanvas.tsx` (`ToolOptions`) |
| Global / transition options | Q | edge options `back` (global edges: return to the interrupted step), `confirm` (ask yes / no first; phrase `CONFIRM_GLOBAL`), `silent` (arrive without speaking); static conversation `skip_response` | `runtime/skills/graph.py` (`_next`, `_fire`), `runtime/harness/engine.py` (confirm question, silent), `runtime/harness/prompts.py`, `runtime/skills/loader.py` (`_reply` fact), `FlowCanvas.tsx` (`EdgeOptions`) |
| Graph checks | M N O | new options validated on save (capture variable / digits / timeout / end keys, transfer destination / type / timeout, tool on_error / retries / timeout, dtmf keys); the canvas badge mirrors them | `runtime/skills/graph.py::_node_option_errors`, `console/src/studio/validate.ts` |
| Hamsa importer | M N O Q | maps `transfer_call` nodes, `dtmf` transitions, global `dtmf` triggers + `globalReturnToSource` / `requiresDoubleConfirm` / `skipResponse`, tool `onErrorBehavior` / `timeout`, static `skipResponse`. Field names for the keypad / transfer / tool options come from Hamsa's docs, not from a real export — check against one | `runtime/platform/hamsa_import.py` |

Also fixed on the way: `_graph_turn` now forgets last turn's classifier answers *before* it looks at the current node (a pending confirmation could otherwise be re-triggered by stale answers).
Tests: new `tests/test_flow_options.py` (29: keypad menu / global key / capture / voice-call keypad handling, transfer warm / cold / plain, tool retry / timeout / fail / spoken result, global return / confirm / silent, importer). Full suite 411 passed (an IVR test failed once earlier in the session under load, passing on reruns). Console `npm run build` clean and the built bundle contains the new controls.
Not checked: the new inspector fields in a browser (Chrome extension was not connected), and keypad capture, a warm / cold transfer or a confirmation on a real phone / browser call.
Left out: `skip_response` for AI-written (prompt) conversation nodes; digit values are not shown in the logs on purpose.

## 2026-10-06 — Flow Agent: gaps P and R

| Component | Gap | Change | Files |
|---|---|---|---|
| Settings node | P | new node type `settings` with `overrides`: `system_prompt`, `voice {ar, en}`, `stt_model`, `llm {model, temperature}`, `call {interrupt, response_delay_ms, inactivity_s, min_interruption_ms, vad_threshold}`; validated ranges (Hamsa's); overrides add up for the rest of the call, an empty value withdraws one; the engine applies the node when the flow reaches it and tells the transport (`Agent.on_settings`) | `runtime/skills/graph.py`, `runtime/harness/engine.py` (`_apply_settings`) |
| What the overrides change at run time | P | model + temperature go to the provider as per-request options (the shared provider is never touched); the system prompt replaces the persona (`persona_override`); the voice goes to the player per language (phrase cache skipped while a voice is set); the STT model is passed per transcription (new optional `model=` on every STT provider); call settings re-configure interruptions, response delay (turn detector), inactivity and barge-in, and start the inactivity watcher if it was off | `runtime/harness/context.py`, `runtime/voice/call.py` (`_on_settings`, `_apply_call_settings`, `_transcribe`), `runtime/voice/player.py`, `runtime/providers/*` |
| A step's own model | P | conversation node `llm {model, temperature}` ("Model for this step") beats the settings node's for that step's replies | `runtime/harness/engine.py` (`_llm_options`), `FlowCanvas.tsx` |
| Agent node | R | new node type `agent`: `agent` (id, `{{ }}` ok), `handoff_history`, `handoff_variables`, `say`; the engine says the line and asks the transport to swap agents (`Agent.transfer_agent`); failure → "On failure" edge, else a person; at most 3 hand-offs per call | `runtime/harness/engine.py` (`_transfer_agent`), `runtime/harness/handoff.py` (`AgentTransfer`, `carry_over`) |
| Swapping the agent mid-call | R | voice / browser call: `VoiceCall.switch_agent` loads the target (published), carries the session over (caller number, language, gender; history / variables on request), swaps providers, TTS, STT hint, skills, tools and call settings, tells the client (`agent_transfer` event) and starts the new agent (`start(resumed=True, greet=…)`: no new call record, no greeting when the conversation came along). Chat: same in `chat.py`. New event `agent.transfer` updates the call row (agent, release) and the live view | `runtime/voice/call.py`, `runtime/server/chat.py`, `runtime/events/schema.py`, `runtime/control/store.py`, `runtime/control/live.py`, `console/src/studio/useTestCall.ts` (shows "now speaking with …") |
| Studio | P R | palette: "Change settings" and "Transfer agent"; inspectors for both (voice picker from the catalog, agent picker from the project's agents); canvas cards; validation badge covers them | `console/src/studio/FlowCanvas.tsx` (`SettingsOptions`, `AgentOptions`), `validate.ts`, `api.ts` |
| Hamsa importer | P R | maps `change_agent_settings` (system prompt, interrupt, response delay, inactivity, min interruption, VAD) and `transfer_agent` (agent id, history, variables, message); voice id, expressiveness, STT model and dictionary ids are listed in the import report; the target agent id is Hamsa's — re-point it. Field names are from Hamsa's docs, not from a real export | `runtime/platform/hamsa_import.py` |

Tests: new `tests/test_flow_agents.py` (17): validation and round trips, cumulative / withdrawn overrides with model, prompt and transport callbacks, a flow without settings nodes passes no options, voice-call settings follow overrides and fall back, STT model and TTS voice per call, agent node success / failure / no handler / cap, carry-over, resumed start, `VoiceCall.switch_agent`, two chats moving between two agents end to end, importer. Full suite 428 passed; console `npm run build` clean.
Not checked: the new inspectors in a browser (Chrome extension was not connected), a real phone / browser voice call changing voice or STT model, or handing a voice call to a second real agent.
Known gaps: expressiveness and voice dictionaries (no provider control), the target agent is loaded as published (a draft test of agent A hands over to B's published version).

## 2026-10-06 — Flow builder: add-node menu and icons like Hamsa's

Compared with Hamsa's flow builder in the browser (their "+" menu, toolbar and node cards).

| Component | Change | Files |
|---|---|---|
| Add-node menu | Panel with the title "Add Node", the subtitle "Choose a node type to add to your workflow", a divider, then one row per node type (bordered icon tile, name, one-line description); same order and wording as Hamsa's: Conversation, Tool, Transfer Call, Transfer to Agent, Router, Set Local Variables, (Go to Skill — ours), Change Agent Settings, End Call. Opens at the top of the canvas, scrolls when the canvas is short | `console/src/studio/FlowCanvas.tsx` |
| Icons | Line icons (lucide-style) replace the emoji / text glyphs: bot, rocket, phone-forwarded, users, git-branch, variable, corner arrow, gear, phone-off, plus, layout. White on a coloured tile on node cards and in the inspector header; coloured on a pale tile in the menu | `console/src/studio/nodeIcons.tsx` (new), `FlowCanvas.tsx` |
| Colours / names | Node types renamed and re-coloured like Hamsa's (Conversation blue, Tool purple, Transfer Call orange, Transfer to Agent green, Router indigo, Set Local Variables green, Change Agent Settings amber, End Call red) | `FlowCanvas.tsx` (`TYPES`) |
| Toolbar | 48 px square buttons: the accent "+" (our red) with a plus icon; (x), layout and settings as outlined buttons with 2 px borders and line icons | `FlowCanvas.tsx` (`ToolButton`) |

Not copied: Hamsa's "Web Tool" entry (needs the web SDK) and its lime brand colour (we keep the Cloud Solutions red). Checked side by side in Chrome; nothing saved.

## 2026-10-06 — Flow builder: node cards like Hamsa's

Compared Hamsa's cards (zoomed canvas) with ours and rebuilt `FlowNodeView`.

| Part of the card | Change | Files |
|---|---|---|
| Frame / header | rounded-2xl card with a border tinted by node type; larger 32 px icon tile, bolder title, line-icon settings button | `console/src/studio/FlowCanvas.tsx` (`TYPES.edge`, `FlowNodeView`) |
| Conversation body | "Prompt:" row with an on-card **Prompt / Static** switch (the active mode filled); a grey box headed "Prompt" or "Static message" with the text; "Extracting:" chips in the accent colour; keypad capture and tool count lines. Switching to Prompt keeps the static text aside, switching back restores it | `FlowCanvas.tsx` (`setMode`, `stash`) |
| Tool body | purple-iconed box "Tool · click to change" with the tool name and arguments, plus "Extracting:" chips for the result values it saves | `FlowCanvas.tsx` |
| Set variables | "(x) N variables" with green chips | `FlowCanvas.tsx` |
| Transitions | "Transitions" heading; each row has an icon and a kind label like Hamsa's — Prompt (sparkles), Equation (Σ), Keypad (#), Reply, Auto-advance (set / settings nodes), Else (router's unconditional edge), On Success / On Failure; text clamped to two lines; the closing row reads "+ Add — drag from ● to connect" (the numbers are gone, ▲▼ still reorder on hover) | `FlowCanvas.tsx` (`rowInfo`, `ROW_KIND`), `nodeIcons.tsx` (sparkles, sigma, hash, zap, code, chat) |

Not copied: the "Advanced Template (truncated)" header and "Extracted / Ignored on this path" pills (we have no per-path extraction), and Hamsa's lime accent (we keep the Cloud Solutions red). Checked in Chrome next to Hamsa's builder with the HMG flow; the Static switch was toggled and nothing was saved.

## 2026-10-06 — Variable System (2.4)

Reviewed Hamsa's 11 variable pages against our code and implemented what was missing.

| Component | Change | Files |
|---|---|---|
| System variables | added `current_timestamp`, `current_day`, `current_month`, `current_year`, `call_type`, `call_start_time`, `agent_id`; `current_time` is now HH:MM; one table (`SYSTEM_VARIABLES`) feeds the runtime list, `GET /api/variables/system` and the console | `runtime/harness/variables.py`, `runtime/skills/loader.py`, `runtime/control/api.py`, `runtime/harness/session.py` (`agent_id`) |
| Custom variables | agent-level `variables` {name: {type, default, description}} in the release bundle (validated); each call gets the defaults with the start message's `params` on top; custom values never replace system ones | `runtime/harness/variables.py` (`build_custom`, `variable_errors`), `runtime/platform/bundle.py`, `runtime/voice/call.py`, `runtime/server/chat.py`, `runtime/server/app.py`, `runtime/harness/handoff.py` |
| Call params | start message `{"event": "start", "params": {...}}` on `/ws` (browser call) and `/ws/chat`; text values are read as the declared number / boolean / JSON; a bad name, a wrong type or > 50 KB → `{"event": "error", "message": …}` and the socket closes | same |
| Naming rules | Hamsa's snake_case rules and reserved system names, checked for declared / `set` / tool-output / keypad variables (graph errors) and custom variables | `runtime/skills/graph.py`, `runtime/harness/variables.py`, `console/src/studio/validate.ts` |
| Template fallback | `{{ name \|\| 'x' }}` works (value missing or empty → fallback) next to Jinja's `default` | `runtime/skills/flow.py` |
| JSONPath | tool `outputs` accept `$.a.b[0].c` and `$`; importer converts Hamsa's | `runtime/skills/flow.py` (`jsonpath_to_path`), `runtime/platform/hamsa_import.py` |
| Typed extraction | `array` and `object` values (JSON) accepted | `runtime/harness/nlu/extract.py` |
| Studio | Global Settings → **Variables** (add / remove, type, default, description; name rules); canvas **(x) panel** lists the system variables (grouped, searchable) and the custom ones; **Insert variable** picker (System · Custom · Collected before this step) on node prompts, static messages and the system prompt; validation: variable names, `{{ x }}` used before it is collected / unknown, Anywhere transitions that depend on collected values | `console/src/studio/GlobalSettings.tsx`, `FlowCanvas.tsx`, `VariablePicker.tsx`, `variables.ts`, `validate.ts`, `api.ts` |

Tests: new `tests/test_variables.py` (36: names, reserved, graph name errors, custom declaration validation, params coercion and refusals, system list, endpoint, carry-over, fallback syntax, JSONPath, array / object values, importer JSONPath, a chat call with defaults / params / bad params); full suite 464 passed. Console build clean.
Checked in Chrome (nothing saved): the HMG flow stays "Valid" (no new warnings), the (x) panel lists the 18 system variables, Global Settings → Variables refuses `call_id` ("a system variable name") and adds `business_name`, the Insert variable picker lists it under Custom.
Not done: phone (IVR) params, a required flag, a live template preview, usage tracking, nested schemas. Hamsa's `$.` → `result.` conversion matches our importer; the Hamsa importer still turns Hamsa's custom variables into an `init` set node (it could fill the new agent-level variables instead).

## 2026-10-06 — Publishing (2.5): public page, embed widget, Share screen

Reviewed Hamsa's two Publishing pages and its live Publish screen, agreed scope with the user (build it, strict default limits) and built it.

| Component | Change | Files |
|---|---|---|
| Storage | table `agent_shares` (agent_id, token, settings, created_by); `share` / `share_by_token` / `put_share` / `delete_share` on both stores; deleting an agent removes its link | `db/schema.sql`, `runtime/platform/store.py` |
| Settings and limits | `ShareSettings` (appearance, embed, limits) validated (colours, origins, param names, ranges); `public_config` = what the page may know (no limits, no name unless shown); `PublicLimiter` (calls at once per link, calls per visitor per hour) | `runtime/platform/share.py` |
| Console API | `GET / PUT / DELETE /api/agents/{id}/share`; publishing needs a published release (409 otherwise); settings errors → 422 with the field names; audit entries `share.published` / `share.unpublished`; unpublishing ends running calls | `runtime/control/share_api.py` |
| Public routes (no sign-in) | `/p/{token}` page (+ `/p/preview`), `/embed.js`, `/public/{token}/config` (CORS open), `WS /ws/public/{token}`: refuses unknown links and foreign origins, applies the limits, ignores the visitor's agent / draft / call id, passes only allowed params, caps the call length, loads the **published** release | `runtime/server/public.py`, `public_page.py`, `runtime/server/app.py` |
| Page | one self-contained HTML page (theme, orb / wave / aura canvas visualizers, transcript overlay, microphone capture with the same worklet / PCM protocol as the console's browser call, states ready / connecting / listening / thinking / speaking, preview mode) | `runtime/server/public_page.py` |
| Embed script | shadow-DOM launcher + overlay iframe; data-attribute overrides; `data-agent-trigger`; fetches the link's embed settings | `runtime/server/public_page.py` |
| Studio | **Share** button next to Publish → `/agents/:id/share`: the form (page, theme, visualizer + presets, advanced appearance, embed with snippet, limits) beside a live preview of the real page | `console/src/pages/Share.tsx`, `studio/*`, `App.tsx`, `api.ts` |
| Setting | `PUBLIC_TRUST_PROXY` (visitor address from `X-Forwarded-For`) | `runtime/config.py` |

Tests: new `tests/test_public.py` (34): settings validation, normalisation, public config, limiter (concurrent / per visitor / rolling hour), console API (publish, update keeps the token, unpublish makes a new one, validation, no release, ends calls, agent delete), public page + CSP, embed script, config + CORS, refused unknown link / foreign site / bad start, a call over the socket (published agent only, params filtered, call cap, cleanup), limits over the socket. Full suite 498 passed. Console build clean.
Checked in Chrome on a throw-away agent (created and deleted again; the real agent was never published): Share screen and live preview follow the settings; publish gave a link and snippet; the public page loaded; the injected embed snippet showed the "Talk to us" pill and the overlay with the page; a scripted socket got `ready` (`pub-…`), the agent's own greeting and ~100 audio frames even though it asked for another agent and the draft; unpublish made the page, config and socket refuse.
Not checked: speaking to the page with a real microphone (the browser's microphone prompt can't be driven here), the page on a phone, and the widget on an external website. Two orphan skill-version rows from the throw-away agent may remain in the database.
Not built: web tools, a logo, "test with microphone" in the preview, a contrast checker, per-link analytics.


## 2026-10-06 — Tools (3): async, Active / Inactive, start / done messages, Token auth

Reviewed Hamsa's four Tools pages against the tool library (see docs/hamsa_parity.md section 3).

| Component | Change | Files |
|---|---|---|
| Token auth | `Authorization: Token <v>` as a new `http.auth.type` (validated, console option) | `runtime/tools/http_tool.py`, `Tools.tsx` |
| Async tools | policy `async` (write / send only): `execute` answers `{success, queued}` at once and runs the call in a background task; outcome logged; pre-hooks (confirmation) still apply | `runtime/tools/executor.py`, `toollib.py`, `types.py`, `catalog.py` |
| Active / Inactive | policy `enabled`: hidden from `llm_tools`, `execute` refuses ("inactive"), flow tool nodes take the failure edge; library status filter + badge; "Active" (used by agents) renamed "In use" | `executor.py`, `Tools.tsx` |
| Lifecycle messages | policy `say_start` / `say_done` `{ar, en}`, templated with `args`; spoken by `_execute_with_filler` (start suppresses the generic filler; done only on success) | `runtime/harness/engine.py`, `Tools.tsx` |
| Console | Active and Async switches, "Request start / Request complete" fields, Token option, status filter | `console/src/pages/Tools.tsx`, `api.ts` |

Tests: new `tests/test_tool_options.py` (12): token auth, field validation, catalog build, inactive hidden + refused, async answers at once and finishes in the background, async failure does not reach the agent, reads ignore async, start / done lines (English, Arabic, template, no done line on failure, start replaces the filler). Full suite 510 passed; console build clean.
Not built: web tools (client-side JS via the SDK / embed widget); not checked in Chrome yet.

## 2026-10-07 — Knowledge Base (4): URL items, rename, delete protection

Reviewed Hamsa's seven Knowledge Base pages against our knowledge base (docs/hamsa_parity.md section 4).

| Component | Change | Files |
|---|---|---|
| URL items | `type: url`, `source_url` column; sitemap discovery (≤ 100 pages, same host); background fetch of the chosen pages (4 at a time), text extracted, chunked and embedded like any item; failures keep the reason, partial failures → completed with errors | `runtime/platform/knowledge.py`, `runtime/control/knowledge_api.py`, `db/schema.sql`, `runtime/platform/store.py` |
| Fetch safety | https only, public hosts only (DNS result checked), redirects re-checked per hop, media files and user-name links refused, 5 MB per page | `runtime/platform/knowledge.py` |
| Rename | `PATCH /api/knowledge/{id}` | `knowledge_api.py` |
| Delete protection | 409 while a published release or a draft searches the item | `knowledge_api.py` |
| Console | "Add Link" card + dialog (Find pages, search, pick), URL type in the filter / table / viewer (source link), "Rename…" row action, delete dialog explains the block | `console/src/pages/Knowledge.tsx`, `api.ts` |

Tests: 7 new in `tests/test_knowledge.py` (url checks, adding a page, unreadable page, redirect to a private host refused, sitemap + multi-page + 100 limit, rename, delete protection). Full suite 517 passed; console build clean.
Not checked in Chrome yet. Not built: form drafts in the browser, separate upload / reading / ingestion failure states.

## 2026-10-07 — Batch Calls (5): outbound calls from a CSV

Reviewed Hamsa's five Batch Calls pages; nothing existed (the sidebar item said "soon"). Owner chose the **dial webhook** design.

| Component | Change | Files |
|---|---|---|
| Storage | tables `outbound_numbers`, `batch_calls`, `batch_recipients`; 14 store methods on both stores | `db/schema.sql`, `runtime/platform/store.py` |
| Core | CSV parsing + phone rules, accepted / rejected rows, schedule validation, daily window (timezone, overnight), signed outbound token, call params from CSV columns, `BatchDialer` (starts due batches, dials inside the window within the concurrency cap, ring timeout, completion, cluster lock) | `runtime/platform/batch.py`, `runtime/config.py` (`PUBLIC_BASE_URL`, `BATCH_LIVE_DIAL`, `BATCH_MAX_CONCURRENT`, `BATCH_RING_TIMEOUT_S`) |
| API | outbound numbers (+ dial token as a secret), validate, create, list / get, rename, delete, pause / resume / cancel / retry, recipients (filter, add, remove), `POST /api/outbound/status` (token only) | `runtime/control/batch_api.py`, `runtime/server/app.py` |
| IVR | `outbound_token` on the voice socket: the batch's agent, `direction = outbound`, CSV variables, recipient linked on connect and completed on close | `runtime/server/ivr.py` |
| Console | Batch calls list + create dialog (CSV step with inline fixing, schedule, window), detail page (summary / recipients / configuration, confirmed actions), outbound numbers card, shared `Modal`, nav item live | `console/src/pages/BatchCalls.tsx`, `BatchCall.tsx`, `Numbers.tsx`, `ui.tsx`, `api.ts`, `App.tsx` |
| Docs | IVR protocol section 9, deployment notes | `docs/ivr_protocol.md`, `docs/deployment.md` |

Tests: new `tests/test_batch_calls.py` (21) + 1 in `tests/test_ivr.py`: phone / CSV rules, variables, schedule, window, token, dialer (simulate mode, live post payload, window + start time, shared concurrency, completion, busy / no-answer / failed / timeouts, dial errors, bearer secret, paused / cancelled), API flows, project scoping, PBX status callback, the outbound socket. Full suite 539 passed; console build clean.
Checked in Chrome on a throw-away server (data deleted again): outbound number saved; CSV with a bad row → 2 accepted / 1 rejected with the reason, fixed inline → 3 accepted; batch created (Scheduled) → dialer ran → Completed with each recipient "simulated" and its variables. Found and fixed: "Retry failed" showed on a batch with nothing to retry. Not verified: a real PBX / dial URL (none connected). Real calls only with `BATCH_LIVE_DIAL=true`.

## 2026-10-07 — Call History (6): duration filter, CSV export, live instructions

Reviewed Hamsa's Call History pages against `/calls` (see docs/hamsa_parity.md section 6): most of it already matched.

| Component | Change | Files |
|---|---|---|
| Duration filter | `duration_op` (between / gt / lt / eq) + seconds on `/api/calls`, validated; SQL on the computed duration; toolbar popover, `dur=` in the URL, part of "Reset" | `runtime/control/api.py`, `runtime/control/store.py`, `console/src/pages/Calls.tsx`, `api.ts` |
| CSV export | "Export CSV" → current page or all filtered results (200 per request, stops at 10,000), visible columns, BOM, spreadsheet-formula guard | `console/src/pages/Calls.tsx` |
| Real-time instructions | `POST /api/calls/{id}/instruction` (≤ 500 chars) → `Runtime.instruct_call` (local call or `hmg_call_control` message to the worker that has it) → `VoiceCall.add_supervisor_note` → `Session.supervisor_notes` → "Live instructions from a supervisor" section of the system prompt; logged as `slot.set supervisor_instruction`; console box on a running call's Overview | `runtime/control/api.py`, `runtime/app.py`, `runtime/platform/sync.py`, `runtime/voice/call.py`, `runtime/harness/{session,context}.py`, `console/src/pages/CallPanel.tsx` |

Tests: new `tests/test_call_history.py` (4): the note reaches the prompt (and only the last five), the call keeps and logs it, the endpoint (ok / not running / empty / too long), duration arguments. The duration SQL was run against the real database read-only (counts for gt / lt / eq / between, with a status filter).
Checked in Chrome on a throw-away server (read-only): `?dur=between:10:60` shows only 10-60 s calls; "All filtered results" produced `call-history-<date>.csv` with the header and 40 rows. Not checked: sending an instruction to a live call (needs a running call; covered by tests only).

## 2026-10-07 — Phone Numbers (7): labels, confirmations, Make outbound call

Reviewed Hamsa's Phone Numbers pages and its Create Outbound Call API; Twilio / SIP-trunk provider setup doesn't apply (no telephony of our own).

| Component | Change | Files |
|---|---|---|
| Label + created date on routes | columns `label`, `created_at` on `phone_routes`; `PUT /api/routes` takes `label` (None keeps, "" clears; ≤ 100) | `db/schema.sql`, `runtime/platform/store.py`, `runtime/control/agents_api.py` |
| Single outbound call | `POST /api/outbound-calls` = a one-recipient batch call with no daily window (`config.always`), started at once; E.164 check, published agent, outbound number | `runtime/control/batch_api.py`, `runtime/platform/batch.py` |
| Phone numbers page | label / agent / created columns, reassign + unassign + remove confirmations, "Edit label", "Make outbound call" dialog (number, agent, parameters), outbound numbers show created date | `console/src/pages/Numbers.tsx`, `api.ts` |

Tests: 3 new in `tests/test_batch_calls.py` (one outbound call end to end, "always" window, label kept / edited / cleared). Full suite below.
Checked in Chrome on a throw-away server (everything created was deleted again): route with label saved, outbound number saved, "Make outbound call" with a parameter → "Call to +966548802968" completed at once (simulated), the three confirmation dialogs.

## 2026-10-07 — Webhooks (12): events, param echo, signing, delivery history

Reviewed Hamsa's Webhooks page and its Webhook Integration guide. Earlier the same day the per-agent webhook was built (URL, bearer
secret, all five events in Hamsa's envelope, retries, test button); this finishes it.

| Component | Change | Files |
|---|---|---|
| Sink | five events (`call.started`, `call.answered`, `transcription.update`, `tool.executed`, `call.ended`) in Hamsa's envelope, ordered per call, 3 attempts, WARNING on failure | `runtime/platform/webhook.py`, `runtime/server/app.py` |
| Param echo | the call's start `params` come back in `call.ended.outcomeResult` and in `call.started` / `call.answered` (read from the live call, not the event log) | `runtime/platform/webhook.py` |
| Headers + signing | `X-Webhook-Event`, `X-Webhook-Id` (same on retries), optional `signing` secret → `X-Webhook-Timestamp` + `X-Webhook-Signature: sha256=<HMAC of "timestamp.body">` | `runtime/platform/webhook.py`, `runtime/platform/bundle.py` validation |
| Delivery history | table `webhook_deliveries` (latest 200 per agent), `GET /api/agents/{id}/webhook/deliveries`; every delivery and every test is recorded | `db/schema.sql`, `runtime/platform/store.py`, `runtime/control/agents_api.py` |
| Test | `POST /webhook/test` accepts the token / signing secret just typed (not saved yet) | `runtime/control/agents_api.py` |
| Console | Global Settings → Call Webhook: signing secret field, events wrap under their help text (they overflowed the panel before), "Recent deliveries" list, test uses typed values | `console/src/studio/GlobalSettings.tsx`, `api.ts` |

Tests: `tests/test_webhook.py` 16 (10 before): param echo, signature + id headers (stable across retries, different per event), secrets
from the store, history rows (delivered / 3 attempts / refused once), the 200-row limit, test endpoint + history over the API. Full suite
below; console builds.
Checked in Chrome on a throw-away server (read only, nothing sent or saved): the Call Webhook section shows the signing field, wrapped
events and an empty "Recent deliveries"; found and fixed the Events row layout. Not run against a real receiver.

## 2026-10-07 — Testing Dashboard (13): test via phone

Reviewed Hamsa's Testing Dashboard (browser test, phone test, transcript, flow debugging, variables, live logs). Everything but the phone
test already existed (and automated test cases + the publish gate go beyond Hamsa's "coming soon").

| Component | Change | Files |
|---|---|---|
| Test via phone | `POST /api/outbound-calls` takes `draft: true` (409 without a draft): a single-call batch named "Test call to …" flagged `config.draft`; the IVR connect loads the agent's draft for it, a normal call stays on the published version | `runtime/control/batch_api.py`, `runtime/server/ivr.py` |
| Studio | Test ▾ → "Test via phone…" opens the call dialog (your number, outbound number, parameters; "Test the draft" decides draft vs published); `MakeCall` is shared with the Phone numbers page and says what to do when there is no outbound number | `console/src/pages/Studio.tsx`, `Numbers.tsx`, `api.ts` |

Tests: 2 new (`test_phone_test_calls_the_draft` in `tests/test_batch_calls.py`, `test_a_draft_phone_test_loads_the_draft` in `tests/test_ivr.py`).
Checked in Chrome (nothing created): Test ▾ shows "Test via phone…" with "Test the draft" ticked; with no outbound number the dialog explains how to add one.
Not tested against a real PBX.

## 2026-10-07 — Post-call analysis (14): summary, sentiment, satisfaction estimates, outcome fields

Closes dashboard gap B and the "Outcome" settings / `outcomeResult` gaps found in the Agents, Call History and Webhooks reviews.

| Component | Change | Files |
|---|---|---|
| Analysis | prompt built from the agent's settings; the model's JSON is validated value by value (sentiment, csat 1-5, nps 0-10, resolved, typed outcome fields); never raises (timeout 40 s, bad JSON, model error → a `failed` row) | `runtime/platform/analysis.py` |
| Sink | `AnalysisSink` (EventBus subscriber): at call end, if the release has `analysis.enabled`, analyses once (≥ 2 caller turns, else `skipped`) and stores it; `result()` lets the webhook wait | `runtime/platform/analysis.py`, `runtime/server/app.py` |
| Storage | table `call_analysis`; `put_call_analysis`, `call_analysis`, `call_analyses` on both stores | `db/schema.sql`, `runtime/platform/store.py` |
| Bundle | `analysis` section validated on save / publish | `runtime/platform/bundle.py` |
| API | `GET /api/calls/{id}` carries `analysis`; `POST /api/calls/{id}/analyze`; `GET /api/analytics/satisfaction` (CSAT = share rated 4-5, NPS = promoters - detractors, sentiment split, resolved) | `runtime/control/api.py` |
| Webhook | `call.ended` waits (≤ 35 s) for the analysis: `outcomeResult` = params + outcome fields, new `analysis` object | `runtime/platform/webhook.py` |
| Console | Global Settings → Outcome (switches, field editor); call drawer "Call analysis" card with Analyze; Dashboard "Caller Satisfaction (estimated)" with Hamsa's CSAT / NPS bands; the "isn't built yet" notes are gone | `studio/GlobalSettings.tsx`, `pages/CallPanel.tsx`, `pages/Dashboard.tsx`, `thresholds.ts`, `api.ts` |

Tests: new `tests/test_analysis.py` (14): settings, prompt, validation of every field type, JSON in prose / fences, each model failure, the
numbers, the sink (stored, off, skipped, failed, cleanup), the webhook payload, the satisfaction and analyze endpoints.
Checked in Chrome on a throw-away server, against the real database and the agent's real model: **Analyze** on a real past call (a caller who
asked about the weather and was transferred) returned an accurate summary, "Neutral", "Not resolved", and no CSAT / NPS (the model said it could
not tell); the dashboard's Satisfaction block then showed that one analysed call; Global Settings → Outcome (nothing saved). That call keeps its
analysis row. Not checked: a live call analysed automatically at its end with the switch on (covered by the sink tests).

## 2026-10-07 — Live listening (15): listen in on a running call

Closes Dashboard gap D and Call History's "live monitoring" row (the drawer said "isn't available yet").

| Component | Change | Files |
|---|---|---|
| Audio tap | `VoiceCall.add_listener()` → a bounded queue of `(source, rate, pcm16)`; the caller's audio is tapped as received, the agent's as it is sent; max 3 listeners; a full queue drops audio for that listener only; cleared on hang-up | `runtime/voice/call.py` |
| Socket | `WS /api/live/listen?call_id=` (console token / session, project role checked, call visible to the project): binary frames = 1 byte source + 2 bytes rate + PCM16, text `ended` / `error`; one `call.listen` audit entry per listener | `runtime/control/api.py` |
| Console | `ListenCard`: Web Audio playback (two gain nodes, per-side mute, speaking dots, rebuffer after an underrun, stops on leave); shown on running calls in the call panel and the dashboard's live drawer, replacing the "not available yet" text | `console/src/listen.tsx`, `pages/CallPanel.tsx`, `pages/LiveDrawer.tsx` |

Tests: new `tests/test_listen.py` (6): listener limit + never blocking the call, the agent's audio tapped in PCM / µ-law, a listener receives both sides and the end of the call (and the queue is released, audit written), a closed socket stops listening, refusals explained, strangers refused; plus a test in `tests/test_ivr.py` with a real call (caller audio and the agent's reply both reach the listener).
Checked in Chrome on a throw-away server with a scripted phone call held open: Live calls → Open call → **Start listening** scheduled 176 audio chunks in 4 s with the audio context running and no errors; **Stop** ended the flow at once and the button returned. The scripted call (a test row "ivr-…" from 0500000001, plus one `call.listen` audit entry) stays in the history. Not heard by a person (the page's audio can't be heard from here); not checked across two workers (not supported).

## 2026-10-07 — Post-call analysis (14.1): compared with Hamsa's live Outcome tab

The owner saw no summary after completing a call and asked for a comparison with Hamsa's Outcome tab (opened on Hamsa: Call History → first call → Outcome, and the agent's Outcome Data Structure editor, nothing saved there).

Cause here: Outcome was switched on in the agent's draft only; calls run on the published version, which had no `analysis`, with no hint in the UI.

| Change | Files |
|---|---|
| Call detail carries `analysis_setup` (version the call ran on, on in that version?, on in the draft?); the Outcome card explains: not published yet (link to the agent) / off for this agent / not analysed yet; copy buttons on the summary and every field card | `runtime/control/api.py`, `console/src/pages/CallPanel.tsx`, `api.ts` |
| Analyze uses the call's version settings, else the agent's draft (so new fields apply) | `runtime/control/api.py` |
| Field types array and object (validated: lists of scalars ≤ 50, objects ≤ 20 keys); "+ Suggested fields" button | `runtime/platform/analysis.py`, `studio/GlobalSettings.tsx` |
| Import: Hamsa's `outcomeResponseShape` → `analysis.fields` (snake_case names, enums, types, ≤ 20), on in the imported agent's first release | `runtime/platform/analysis.py` (`fields_from_schema`, `settings_from_schema`), `hamsa_import.py`, `agents_api.py` |

Tests: 6 more in `tests/test_analysis.py` (20 in total): array / object, schema conversion, settings from a schema, import, the "why" setup, Analyze with the draft's fields. Full suite 591 passed; console builds.
Checked in Chrome (throw-away server): the owner's latest call (Oct 7, 11:32) now shows "Not published yet … ran on a published version, which has no analysis" with the Analyze button. Not done on their agent: publishing (their call).

## 2026-10-07 — Publish gate: a model-provider timeout no longer fails a test case

Owner report: publishing the new agent failed the gate on `book_named_specialty_en` — `tools_in_order … booked … no_error: APITimeoutError('Request timed out.')`.

Cause: not the agent. The case plays a 12-turn conversation against the real model (Groq); one slow answer past the 10 s request timeout broke the conversation off, so the tools "missing" and the appointment "not booked" were only consequences. Re-running it three times locally gave 1 pass, 2 timeouts (`APITimeoutError`, `ReadTimeout`) — the same case passes when the provider answers in time.

| Change | Files |
|---|---|
| `run_case` plays the whole case again (up to 3 attempts) when it dies on a provider error (timeout, dropped connection, rate limit, 5xx); real errors and failed checks count at once | `evals/runner.py` (`is_provider_error`, `ATTEMPTS`, `_run_case_once`) |
| If every attempt hits the provider, the result is `provider_problem` and the `no_error` detail says "the model provider failed on all 3 attempts … not a verdict on the agent; run the case again" | `evals/runner.py` |
| Gate / run progress show that line alone (not the consequences) and keep 260 characters instead of 200 | `runtime/platform/gate.py` (`failed_lines`), `runtime/control/agent_evals_api.py` |

Tests: 5 new in `tests/test_evals.py` (which errors are the provider's, retried until it passes, gives up after 3 with the message, real failures not retried, gate line). Full suite 596 passed.

### Follow-up (same day): the retry alone was not enough — the real cause was the test's simulated caller

After the first fix the owner's gate still failed `book_named_specialty_en` on all 3 attempts (`ReadTimeout('')`). Timing every model call in one run showed it: the **agent's** calls were fast (0.2-3 s to first token, worst 5.7 s) while the **simulated caller** (the test's fake patient; a reasoning model, no tools, tiny prompts) often needed 4-6.5 s to start answering, so a bad draw crossed the 10 s read limit. Test tooling was held to a live call's time limit.

| Change | Files |
|---|---|
| the simulated caller and the judge wait up to `TOOLING_TIMEOUT_S` = 60 s (a live call's model still has 10 s) | `evals/runner.py` |
| the simulated caller retries its own slow / failed reply (up to 3 tries; parse errors as before) instead of ending the conversation | `evals/caller.py` |
| the provider-error test moved to `evals/errors.py` (shared) | `evals/errors.py`, `evals/runner.py`, `evals/caller.py` |

Tests: 3 more in `tests/test_evals.py`. Full suite 598 passed.
Verified: the gate case run four times in a row with the fix passed every time (49-86 s, no timeouts); before it passed about one run in three.

## 2026-10-07 — API keys (16)

The sidebar item said "soon"; without keys nothing outside the console could use the APIs built in the earlier modules (outbound call, satisfaction analytics, batch calls …).

| Component | Change | Files |
|---|---|---|
| Storage | table `api_keys` (hash only, prefix, scope, expiry, last used, revoked); store methods on both stores | `db/schema.sql`, `runtime/platform/store.py` |
| Principal | kind `key`: an admin of its own project only; `resolve` recognises `hmg_` keys first and never lets a bad key fall through to the open console | `runtime/control/accounts.py` |
| Guard | `Authorization: Token` / `Bearer` / `X-API-Key`; read-only keys can't write; keys can't manage keys / people / projects / secrets; bound to their project; rate limit; WebSocket live feed bound to the project, listening refused | `runtime/control/api.py`, `runtime/config.py` (`API_KEY_RATE_PER_MIN`) |
| API | `GET / POST /api/api-keys`, `DELETE /api/api-keys/{id}`, `GET /api/whoami` (audited, key never logged) | `runtime/control/keys_api.py`, `runtime/server/app.py` |
| Console | API keys page (list, create with access + expiry, key shown once with `curl` examples, revoke), sidebar item enabled | `console/src/pages/ApiKeys.tsx`, `api.ts`, `App.tsx` |

Tests: new `tests/test_api_keys.py` (11): shown once / hash only, creation rules and the 20-key limit, resolving (valid, revoked, expired, unknown never open, last-use throttle), the three header forms, revoke and expiry, read-only scope, the forbidden management paths, project binding, audit under the key's name, per-key rate limit, no listening. Full suite 608 passed (+1 existing test skipped: needs a database); console builds.
Checked in Chrome on a throw-away server: the API keys page (list with status badges and last-used times), the create dialog, the key shown once with `curl` examples, revoke with confirmation. A read-only key made there, used with `curl`: `whoami` ok; GET agents / routes 200; changing a route 403 "read-only"; making a key 403; another project 403; a made-up key 401; the Token, Bearer and X-API-Key forms all 200; after revoking, 401 at once. (A revoked key stays in the list as "Revoked" — there is no delete.)
