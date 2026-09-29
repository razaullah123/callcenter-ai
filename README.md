# HMG Voice Agent

Voice-to-voice customer care agent for Dr. Sulaiman Al Habib Medical Group — Najdi Arabic and English,
Groq STT / LLM / TTS, skills-based agent harness over the HIS MCP tools, IVR WebSocket integration and an operations console.

| | |
|---|---|
| Build plan & decisions | [PLAN.md](PLAN.md) |
| Flow specs | [docs/flows/](docs/flows/) |
| IVR protocol (for the IVR team) | [docs/ivr_protocol.md](docs/ivr_protocol.md) |
| Deployment & operations | [docs/deployment.md](docs/deployment.md) |

## Quick start (development)

```bash
python -m venv venv && venv\Scripts\activate          # Windows
pip install -e ".[dev]"
cp .env.example .env                                   # fill in keys / URLs
python -m runtime.server                               # http://localhost:8080/console
```

| Command | What |
|---|---|
| `python scripts/chat.py` | Text chat with the agent (`--live-auth`, `--live-booking` for real HIS calls) |
| `python scripts/voice_sim.py scripts/flows/voice_en.txt` | Simulated voice caller |
| `python scripts/ivr_sim.py scripts/flows/ivr_ar_transfer.txt --echo 0.3` | Simulated IVR line (exact wire protocol) |
| `python -m evals --judge` | Conversation evals (15 cases) |
| `python scripts/load_test.py --calls 50` | Concurrency load test (against a `PROVIDER_OVERRIDE=fake` server) |
| `pytest` | Unit / integration tests |
| `cd console && npm run dev` | Console with hot reload |
| `docker compose up -d --build` | Container deployment |
