# HMG Voice Agent — Deployment & Operations

Audience: the team that hosts and operates the voice agent.

## 1. What runs

A single container image serves everything on port 8080:

| Path | Purpose |
|---|---|
| `/ws/voice-pipeline` | IVR telephony endpoint (see [ivr_protocol.md](ivr_protocol.md)) |
| `/ws` | Browser / JSON voice endpoint (console playground) |
| `/console` | Operations console (dashboard, live calls, history, logs, providers, skills, evals) |
| `/api` | Console API |
| `/health` | Liveness + capacity signals (`active_calls`, `loop_lag_ms`, `config_version`) |

It needs **Postgres 16 with pgvector** (calls, events, audit, config and skill versions, hospitals and locations).

## 2. Network access the host needs

| Destination | Why |
|---|---|
| `api.groq.com` (HTTPS) | Speech-to-text, LLM, text-to-speech |
| HIS MCP server (`MCP_SERVER_URL`) | Patient lookup, OTP, clinics, slots, booking |
| Embedding service (`EMBEDDING_URL`, internal) | Location search. If unreachable, the agent falls back to name matching automatically. |
| Postgres | Data |
| Inbound 8080 from the IVR and the console users | |

> During development, the organization network blocked Groq and the external network couldn't reach the
> embedding service. **Production needs all of them from one host**, or an egress proxy for Groq.

## 3. First deployment

```bash
cp .env.example .env              # fill in: Groq key, MCP URL + token, EMBEDDING_URL, AUTH_SECRET, CONSOLE_TOKEN
echo "POSTGRES_PASSWORD=<strong password>" >> .env
docker compose up -d --build
# one-off: copy hospitals + locations (with embeddings) from the source database
docker compose exec agent python scripts/clone_reference_data.py --source "<SOURCE_DATABASE_URL>"
docker compose restart agent
curl http://localhost:8080/health
```

Set `TOOLS_MODE=live` for real patients. **In live mode the server refuses to start unless `CONSOLE_TOKEN`
and `AUTH_SECRET` are set.** (`ALLOW_INSECURE_LIVE=true` overrides this; use it only for a supervised test.)

## 4. Capacity & scaling (measured)

The load test uses fake speech/LLM providers with realistic delays and real speech audio, so it measures only this server
(`scripts/load_test.py`). All callers were active at the same time:

| Concurrent calls (1 process) | Turn end → first audio p50 / p95 | Playback gaps > 250 ms | Event-loop lag p95 |
|---|---|---|---|
| 10 | 844 / 899 ms | 0 | 44 ms |
| 25 | 914 / 1343 ms | 9 | 139 ms |
| 50 | 980 / 1604 ms | 16 (of ~15 000 frames) | 189 ms |
| 100 | degraded: 24 of 100 connections dropped | — | 320 ms |

- **Plan ~40–50 concurrent calls per server process.** Set `WORKERS=N` to run N processes in one container, and/or run
  several containers behind a load balancer that supports WebSockets. Calls are independent, and every process shares Postgres.
- The capacity signal is `/health` → `loop_lag_ms`. Sustained values above ~150 ms mean the process is saturated.
- Voice activity detection (Silero, ONNX) runs in a thread pool across cores. The IVR's 8 kHz audio needs no resampling.
- External latency (Groq STT / LLM / TTS) adds to these numbers. See the console dashboard, "Latency by stage".
- The console's **Live calls** view shows the calls of the process it is connected to. History, stats and logs are
  shared through the database. A unified live view across processes needs Redis pub/sub (not yet built).

## 5. Patient data (PDPL) & security

| Control | Where |
|---|---|
| Caller must pass OTP verification before any patient-data tool runs | Enforced in code (auth gate) |
| Patient records are never shown to the LLM; the LLM can't choose the patient ID | Harness injects IDs from the verified session |
| Mobile numbers, OTP codes, names and dates of birth are masked in logs and events | Event redactor |
| **Audit trail**: every patient-data read / write / send (call, tool, patient ID, outcome, confirmed, verified) | `patient_access_audit` table, **separate from logs**, kept `AUDIT_RETENTION_DAYS` (730) |
| **Retention**: transcripts and events, JSONL logs and eval runs deleted after `EVENT_RETENTION_DAYS` (90); call summaries (no content) kept | Maintenance task every 6 h |
| Console and API require `CONSOLE_TOKEN`; IVR requires a JWT (`AUTH_SECRET`) and a blacklist check | |
| Secrets live only in `.env` / the provider config; never returned to the browser | |
| Writes (booking, cancel, complaint) need an explicit spoken or keypad "yes" after a read-back | Enforced in code |

**Data residency:** transcripts, audit and config stay in your Postgres (host it in-Kingdom). Caller audio and text are
processed by Groq. Confirm with Groq which region serves your account and put a data-processing agreement in place.
The provider layer is pluggable (Console → Providers), so an in-Kingdom STT, LLM or TTS endpoint can be swapped in
without code changes. Recommended: Postgres encryption at rest, TLS in front of port 8080 (reverse proxy), and restricting
`/console` and `/api` to the operations network.

## 6. Operations runbook

| Task | How |
|---|---|
| Change STT / LLM / TTS model or voice | Console → Providers → Test → Save as new version (new calls only) |
| Roll back a provider change | Console → Providers → Version history → Activate |
| Change what the agent says / does | Console → Skills → edit → Validate → Save version (applies to new turns) |
| Roll back a skill | Console → Skills → Versions → Diff → Restore → Save |
| Check quality after a change | Console → Evals → Run (or `python -m evals --judge`) |
| Investigate a call | Console → Call history → call (conversation, latency per turn, full event timeline) |
| Refresh hospitals / locations | `python scripts/clone_reference_data.py --source …`, then restart |
| Health | `GET /health` (Docker `HEALTHCHECK` uses it) |
| Logs | `docker compose logs agent`; structured events in Postgres (`call_events`) and `logs/events-*.jsonl` |

## 7. Known limitations / next steps

- Keypad (DTMF) is supported on `/ws` but the IVR protocol has no DTMF message yet (pending the IVR team).
- Transfer destination defaults to the chosen branch's `base_extension` unless `IVR_TRANSFER_DESTINATION` is set.
- Six skills (manage appointment, send info, insurance, medical reports, post-visit, complaints) are drafts pending flow specs.
- Unified live view across processes (Redis) and horizontal autoscaling are not built yet.
