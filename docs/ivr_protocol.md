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
| text | `{"action": "transfer", "destination": "<extension or queue>"}` asks the IVR to transfer the call to a human. The agent says a short handoff sentence before sending this. |
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
| `AUTH_DATABASE_URL` | `SOURCE_DATABASE_URL` | Database with `blacklisted_tokens` / `white_listed_numbers` |
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
