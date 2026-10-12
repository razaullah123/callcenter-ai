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

- Access control, still open: a platform-owner list managed from the console (today: `PLATFORM_OWNER_EMAILS`). Done 2026-10-08: per-agent access, read-only Viewer role, and `INVITED_USERS_CAN_CREATE_PROJECTS=false` to stop invited users creating projects. Not yet checked by hand in the browser.

## 4. Not verified yet (built and covered by tests, never run end to end)

- Web tools from a real website with a real microphone call (`docs/examples/web-tools-demo.html`).
- Recording and web-tools behaviour under several server workers.

## 5. Flow nodes: small gaps against Hamsa (2026-10-08)

- Put the caller on hold while a tool node runs.
- AI-generated (prompt-type) lines for tool processing messages and end-call farewells; today they are fixed text.
- Change-settings node: expressiveness and pronunciation dictionaries (need those settings first).
- Timeout on the transfer-to-agent node; per-variable type and description in the set node.

## 6. After removing the platform symptom check (2026-10-11)

- `python -m evals` without `--agent` runs the old multi-skill agent from `skills/` (default settings have no main flow), which production does not use. Its three symptom cases (`red_flag_chest_pain_ar`, `symptom_upfront_sympathy_ar`, `clinic_named_no_sympathy_ar`) now fail there, because the sympathy rules were added to the one-flow `hmg_call` agent only. Run the evals with `--agent hmg-care` (they pass 5 of 5 on the published agent), or make the repo copy default to the one-flow agent, or retire the old skills.
- Two other agents (HMG Care (Hamsa rebuild) and test-agent) still carry the retired `red_flag_mode` in their releases. It is ignored now, so they get no symptom handling: add the sympathy rule to their prompts if they need it.
- The agent names the first doctor of the search result in only about half of the runs, though the step says "Suggest the FIRST doctor only" (new optional eval check `suggested_list_first`).
- The next server start imports the updated repo copies of `_persona` and `hmg_call` (repo sync) as new versions with identical text and publishes one identical release of `hmg-care`.

