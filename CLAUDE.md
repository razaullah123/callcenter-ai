# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Voice-to-voice call center agent for Dr. Sulaiman Al Habib (Najdi Arabic + English), growing into a multi-agent
platform (Hamsa-like): every agent's prompts, flow graph, skills, tools, LLM / STT / TTS live in Postgres and are
edited from the React console. `PLAN.md` is the source of truth for phases, decisions and a dated progress log —
append a progress entry there when finishing a piece of work.

## Commands

Windows dev box; the venv is `venv/` (use `venv/Scripts/python.exe` from bash).

```bash
pip install -e ".[dev]"
python -m runtime.server [port]          # API + console at http://localhost:8080/console (default port 8080)
pytest                                   # all tests (asyncio_mode=auto, ~10 s, no network)
pytest tests/test_graph.py -k one_flow   # single file / test
python -m evals                          # conversation evals on the repo copy (real Groq LLM, fixture HIS)
python -m evals --agent hmg-care --draft # same, on an agent's draft from the DB
python -m evals --case auth_skip_attempt --mode script
python flows/hmg_call/build.py           # regenerate flows/hmg_call/flow.yaml (the one-flow HMG graph)
python scripts/chat.py                   # text chat with the agent
cd console && npm run dev                # console with hot reload;  npm run build  (tsc -b + vite) to type-check
```

Working conventions with this user:
- The user runs their own server on **8080** — never start/stop it. Use another port (e.g. 8091/8092) for your own
  checks and stop it afterwards; don't kill servers you didn't start.
- `.env` may have `HYBRID_LIVE_AUTH=true` / `HYBRID_LIVE_BOOKING=true`: test calls then send real OTPs and book
  **real** appointments. Evals always use the fixture backend (`evals/fixtures.py`).
- The agent uses only its own database `hmg_voice_agent` (`DATABASE_URL`). Never read `ai_agent_patient_appointment`
  (`SOURCE_DATABASE_URL`) at run time — if data from it is needed, add the table to `db/schema.sql` and to
  `scripts/clone_reference_data.py` and import it (a guard test in `tests/test_ivr.py` enforces this).
- For multi-line edits from bash, write a Python script to the scratchpad and run it (heredocs with quotes/backticks
  break).

## Architecture

**Call path.** `runtime/server/app.py` (FastAPI) hosts the browser call WS, the IVR WS (`server/ivr.py`, protocol in
`docs/ivr_protocol.md`), the text test channel `/ws/chat` (`server/chat.py`), the control API and the built console.
`runtime/voice/` does VAD → STT → harness → TTS player with barge-in. `runtime/app.py` is the shared Runtime
(pools, loader, providers, schema apply from `db/schema.sql` on start — schema changes are idempotent
`CREATE/ALTER ... IF NOT EXISTS` appended there).

**Platform (`runtime/platform/`).** An agent **release** is an immutable bundle (schema 2: models, knobs, phrases,
skill version pins, tool policies). `AgentLoader` builds a `LoadedAgent` (providers, SkillSet, ToolExecutor,
settings) and a call keeps it to the end. Drafts live in `agents.draft`; publish creates a release, activate rolls
back. `seed.py` migrates the repo into the DB on first start and, with `PLATFORM_REPO_SYNC`, re-imports changed
`skills/`, `config/tools.yaml` and default phrases as new versions/releases. Secrets are Fernet-encrypted under
`MASTER_KEY`, referenced as `{"secret": NAME}`. `sync.py` propagates changes across workers via Postgres
LISTEN/NOTIFY. `store.py` has `PgStore` and an equivalent `MemoryStore` used by tests — keep both in step.
`gate.py` is the publish gate (12.7): per-agent test cases must pass on the exact draft (content hash) or publish
needs an audited override reason.

**Harness (`runtime/harness/`).** `engine.py` runs a turn: context building (`context.py`), LLM streaming with
hedging, tool calls, and flow-graph execution; `policy.py` enforces verification, read-back confirmation for writes
(the "yes" must come in a later turn), unbacked-claim blocking ("booked" only if a tool backing it succeeded).
Tool-specific behaviour is **not** coded in the harness (a guard test enforces no tool names there): it comes from
tool policies (`role`, `args`, `hooks`, `backs`, `confirm`, `idempotent`, `success_line`) in `config/tools.yaml` /
the DB, with named hook handlers in `runtime/packs/hmg.py`.

**Skills and flows (`skills/`, `flows/`, `runtime/skills/`).** A skill is a folder with `SKILL.md` (frontmatter +
prompt, `## ar` / `## en` sections) and optional `flow.yaml`. `_persona` is the agent-wide system prompt. Flows are
graphs (`runtime/skills/graph.py`): node types conversation / router / set / tool / transfer / end / skill; edges are
tried in order (own edges, then `*` global edges, first match wins) with conditions filled / empty / equals / stage /
llm / replied / all / any and tool `result` success/failure; tool nodes map result paths to slots (`outputs`). Set
values, tool args, node instructions and the persona may be sandboxed Jinja templates (`skills/flow.py::render`, with
built-ins such as `current_datetime`, `call_lang`, `userNumber`). Typed variables are extracted once per turn by
`harness/nlu/extract.py::classify_and_extract`. Old step-style flows convert to graphs automatically. Knobs
`main_flow` / `entry_skill` / `require_verification` select one-flow mode (e.g. `flows/hmg_call`, which is generated
by its `build.py` from the tested step texts of `skills/authenticate` and `skills/book_appointment`).

**Tools (`runtime/tools/`).** MCP client for the HIS tools, HTTP API tools, `TOOLS_MODE` live | hybrid | mock
(`hybrid.py` routes reads live and simulates patient/auth/writes unless the `HYBRID_LIVE_*` flags are set). The
executor caches reads, joins in-flight calls and de-duplicates idempotent writes per call. API (`source: http`) tools
follow TOOLS_MODE too: mock calls nothing, hybrid simulates their write / send calls (`tools/http_tool.py::simulated`).
Their `http.auth` (bearer / basic / api_key, parts may be `{"secret": NAME}`) becomes a header in `build_request`.

**Control API + console.** `runtime/control/*_api.py` (agents/drafts/publish/routes, test cases + gate, tool
library, connections/providers/secrets, evals, live events). The console (`console/`, React 19 + React Query +
Tailwind 4 + `@xyflow/react`) — Agent Studio is `pages/Studio.tsx` with `studio/FlowCanvas.tsx` (canvas, node
inspector), `studio/TestPanel.tsx` + `useTestCall.ts` (browser call / chat tests with live logs, follow canvas) and
`studio/TestsTab.tsx` (test cases, publish dialog, audit). API types/calls are all in `console/src/api.ts`.
Import Agent: `runtime/platform/hamsa_import.py` converts a Hamsa agent's JSON (what Hamsa's flow builder loads) into a
new agent (`POST /api/agents/import`); Hamsa's `.hamsa` export files are encrypted with Hamsa's key and can't be read.

**Evals (`evals/`).** `runner.py` plays a case (scripted lines or an LLM-simulated caller with goal/facts) against
the real harness with the fixture backend, then `checks.py` applies the `expect` checks (tools in order, booked,
verified, language, gender agreement, no medical advice, words/questions per reply). Repo cases in `evals/cases/*.yaml`
were imported once as the HMG agent's DB test cases; LLM-caller cases are somewhat flaky, so read failing
transcripts in `evals/reports/` before concluding the agent regressed.
