# Voice agent — IVR WebSocket protocol

Endpoint for the telephony / IVR integration. It keeps the wire protocol of the existing
`/ws/voice-pipeline` integration, so the IVR side needs no changes apart from the
outbound audio notes in section 4.

## 1. Connect

```
ws://<host>:8080/ws/voice-pipeline?phone_number=<number>&access_token=<JWT>
```

| Parameter | Meaning |
|---|---|
| `phone_number` | The caller's number, e.g. `+966548802968` or `0548802968`. If it has **8 digits or fewer** it is treated as a **PBX extension**: the call is routed to that branch (matched on `projects.base_extension`, using the leading 4 digits, or the leading 3 if 4 don't match). A literal `+` that isn't URL-encoded arrives as a space; the server repairs it. |
| `access_token` | JWT signed with **HS256** using the shared `AUTH_SECRET`. It is rejected if expired, badly signed, or if its `jti` is in `blacklisted_tokens`. These are the same tokens the IVR uses today. |

If the token is rejected, the socket is closed with **code 1008** (`Unauthorized`).
Optional: with `IVR_WHITELIST=true`, numbers that aren't in `white_listed_numbers` hear a
short rejection message and the socket is closed. Extension calls are exempt.

## 2. IVR → agent

| Frame | Content |
|---|---|
| **binary** | The caller's audio: **raw PCM16, little-endian, mono, 8 kHz**, sent as a continuous stream. No WAV header. Any frame size works; 20 ms (320 bytes) is recommended. |
| text | `{"type": "ping"}` keep-alive. There is no reply. |
| text | `{"type": "init", "sample_rate": 16000}` is optional. It changes the inbound sample rate (default 8000). |

Keep sending audio while the agent talks, including silence. The caller can interrupt the agent at any time.

## 3. Agent → IVR

| Frame | Content |
|---|---|
| **binary** | Agent speech: **each frame is a complete WAV file** (44-byte RIFF header + PCM16 mono), about **300 ms** of audio, sent **in real time**. Play each piece as it arrives. This covers the greeting, fillers such as "لحظة أشيك لك", and replies. |
| text | `{"action": "transfer", "destination": "<extension or queue>"}` asks the IVR to transfer the call to a human. The agent says a short handoff sentence before sending this (a flow's "cold" transfer sends it with no sentence). A flow's transfer node may name its own `destination` (E.164 number or extension) and add optional `"timeout_s": <1-60>` (how long to let it ring) and `"headers": {"X-Name": "value"}` (SIP headers to attach); they are omitted when the flow doesn't set them, so IVRs that ignore unknown keys are unaffected. |
| close | The agent closes the socket after its goodbye, when the call is finished. |

## 4. Outbound audio: rate and pacing (changes from the previous implementation)

- Replies are **resampled to 8 kHz** before sending (`IVR_OUTBOUND_RATE=8000`), so the IVR doesn't have to resample. The WAV header always states the real rate. Set `IVR_OUTBOUND_RATE=0` to receive the TTS provider's native rate (24 kHz) instead.
- Pieces are **paced in real time** with about 120 ms of lead. The IVR's playback buffer therefore never holds more than one or two pieces.

## 5. Interruptions (barge-in)

There is **no "clear" message**. When the caller speaks over the agent, the agent stops
sending audio immediately. Because the pieces are paced in real time, the caller hears at most
the piece currently playing (about 300 ms). If your player buffers more than that, drop
buffered agent audio when inbound speech starts.

The agent cancels echo of its own voice on the uplink (WebRTC AEC3). It ignores interruptions
in the first 700 ms of each agent utterance, and interruptions that are mostly echo.

## 6. Keypad (DTMF)

Not part of this protocol yet. The agent already accepts keypad entry of the mobile number and
OTP on its other endpoint (`/ws`, `{"event":"dtmf","digit":"5"}`). If the IVR can forward DTMF,
please tell us the format and we'll add it here. This matters because long Arabic digit strings
are the least reliable input over 8 kHz audio.

## 7. Configuration (agent side, `.env`)

| Setting | Default | |
|---|---|---|
| `AUTH_SECRET` | — | Must equal the IVR auth service's secret. If it is unset, tokens are **not verified** (development only). |
| `DATABASE_URL` | — | The voice agent's own database; `blacklisted_tokens` / `white_listed_numbers` are imported into it by `scripts/clone_reference_data.py` (re-run it, or write revocations there, to block a token) |
| `IVR_INBOUND_RATE` | 8000 | |
| `IVR_OUTBOUND_RATE` | 8000 | 0 = keep the TTS provider's rate |
| `IVR_CHUNK_MS` | 300 | Outbound WAV piece length |
| `IVR_AEC` | true | Echo cancellation |
| `IVR_BARGE_IN_GRACE_MS` | 700 | |
| `IVR_WHITELIST` | false | |
| `IVR_TRANSFER_DESTINATION` | — | Human-agent destination. If empty, the chosen or dialed branch's `base_extension` is used. |

## 8. Open points for the IVR team

1. **Transfer destination:** which value should `destination` hold for a transfer to a human agent (a queue, an extension, the branch operator)?
2. **Wideband audio:** can the stream be 16 kHz (send `{"type":"init","sample_rate":16000}`)? Arabic speech recognition, especially of digits, is noticeably better than at 8 kHz.
3. **DTMF forwarding** (section 6).

## 9. Outbound calls (batch calls)

The platform has no telephony of its own. Batch calls (console → Batch calls) work like this:

1. Under **Phone numbers → Outbound numbers** each from-number gets a **dial URL** (and an optional bearer token).
2. For every recipient the platform sends `POST <dial URL>` with JSON:
   `{"to": "+9665…", "from": "+9661…", "name": "Sara", "variables": {"city": "Riyadh"}, "batch_call_id": "bc_…", "recipient_id": 12, "outbound_token": "<JWT>", "ws_url": "wss://<host>/ws/voice-pipeline", "status_url": "https://<host>/api/outbound/status"}`.
   Answer 2xx to accept. Any other answer marks the recipient failed. You may answer `{"status": "busy" | "no_answer" | "failed"}` at once.
3. Your IVR / PBX places the call. **When the callee answers**, connect to `ws_url?phone_number=<to>&access_token=<the usual JWT>&outbound_token=<outbound_token>`. The audio protocol is the one above. The agent that was chosen for the batch call answers (its published version), with `direction = outbound`, the recipient's CSV columns as call variables, and `agent_number` = the from-number. With the "wait for the user" knob set to `outbound`, it lets the callee speak first.
4. A call that never gets answered: `POST status_url` with `{"outbound_token": "...", "status": "busy" | "no_answer" | "failed", "reason": "..."}` (no other sign-in). If nothing arrives within `BATCH_RING_TIMEOUT_S` (90 s) the recipient becomes no-answer.
5. When the socket closes the recipient is completed (duration and call id are kept; the call appears in Call history).

| Setting | Default | Meaning |
|---|---|---|
| `BATCH_LIVE_DIAL` | false | false: **nothing is dialed**, every recipient is marked completed "simulated". Set true only with a real dial URL |
| `BATCH_MAX_CONCURRENT` | 5 | calls in progress at once, all batches together |
| `BATCH_RING_TIMEOUT_S` | 90 | unanswered after this → no-answer |
| `PUBLIC_BASE_URL` | — | address the IVR reaches this server on; goes into `ws_url` / `status_url` |
