# Remaining items (Hamsa parity and follow-ups)

Written 2026-10-07. Everything not listed here is built and tested; see `docs/hamsa_parity.md` for the module-by-module tables and
`docs/hamsa_parity_worklog.md` for what changed. Items are grouped by what they need.

## 1. Need a decision or data from the owner

| Item | What is needed | Notes |
|---|---|---|
| Cost per call | prices for LLM, speech-to-text, text-to-speech and telephony (per minute / token) | Hamsa shows credits per call and in the dashboard; we have no cost column. The call panel would show Cost instead of / next to LLM and STT |
| Recording storage beyond a local folder | decide only if S3 or compression is wanted | Today: encrypted WAV in `RECORDINGS_DIR`, 30 days (about 2 MB a minute before encryption) |

## 2. Call recordings: follow-ups

- Signed download link for `conversationRecording` in the `call.ended` webhook (still null).
- Per-call "do not record" option; recording of listen-in audio.
- Check Download and Delete in the player's ⋮ menu on a test call (not tried yet).
- Phone (IVR) calls: only web calls have been recorded so far. Try one real phone call.
- S3-compatible storage backend and Opus compression, if the folder becomes a problem.

## 3. Smaller leftovers from the Hamsa review

- Real post-call survey (today's CSAT / NPS are estimates read from the transcript).
- Sentiment column in Call History.
- Live listening across several server workers (works on one worker today).
- Project-level webhook (one URL for all agents) and resending a failed delivery.
- Agent-number column in the call list; page-size selector.
- QUEUED status and a per-batch concurrency limit for batch calls.
- Test against a real PBX / phone system.
- Call panel fields: Hamsa shows Cost and Agent Number; ours shows LLM and speech-to-text (Cost depends on section 1).

- Access control: per-agent access inside a project, a read-only role, an option to stop invited users creating their own projects, and a platform-owner list managed from the console (today: `PLATFORM_OWNER_EMAILS`).

## 4. Not verified yet (built and covered by tests, never run end to end)

- Web tools from a real website with a real microphone call (`docs/examples/web-tools-demo.html`).
- Recording and web-tools behaviour under several server workers.
