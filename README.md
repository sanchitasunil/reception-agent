# Arogya Clinic — Voice Agent

AI phone receptionist for Arogya Clinic (Koramangala, Bangalore). Answers inbound calls via Twilio, handles appointment booking and FAQs.

**Stack:** LiveKit Agents · Deepgram Nova-3 (STT) · Gemini Flash (LLM) · Murf Falcon (TTS) · Silero (VAD)

---

## Setup

### 1. Clone and enter the project

```bash
git clone <repo-url>
cd reception-agent
```

### 2. Create and activate a virtual environment

```bash
python -m venv venv
# macOS / Linux
source venv/bin/activate
# Windows
venv\Scripts\activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment variables

```bash
cp .env.example .env
```

Open `.env` and fill in:

| Variable | Where to get it |
|---|---|
| `LIVEKIT_URL` | LiveKit Cloud dashboard → your project |
| `LIVEKIT_API_KEY` | LiveKit Cloud → Settings → API Keys |
| `LIVEKIT_API_SECRET` | Same as above |
| `DEEPGRAM_API_KEY` | console.deepgram.com |
| `GOOGLE_API_KEY` | aistudio.google.com → API keys |
| `MURF_API_KEY` | murf.ai → Settings → API |
| `TWILIO_ACCOUNT_SID` | console.twilio.com |
| `TWILIO_AUTH_TOKEN` | console.twilio.com |
| `TWILIO_PHONE_NUMBER` | Your Twilio phone number in E.164 format |
| `TWILIO_WHATSAPP_FROM` | Twilio WhatsApp sender (sandbox: `whatsapp:+14155238886`) |
| `LIVEKIT_SIP_URI` | LiveKit Cloud → Telephony → SIP URI (hostname only) |

> **Note on voice:** The agent uses `en-IN-anisha` as the Murf voice. Check [murf.ai/voices](https://murf.ai/voices) for available Falcon voice IDs and update `agent.py` if needed.

### 5. Download the Silero VAD model

```bash
python agent.py download-files
```

### 6. Run the agent in dev mode

```bash
python agent.py dev
```

The agent connects to the [LiveKit Agents Playground](https://agents-playground.livekit.io/) where you can test it with your microphone — no Twilio required at this stage.

---

## Telephony setup (Phase 2)

Once Priya works in the playground, connect a real phone number:

1. Fill in `TWILIO_*` and `LIVEKIT_SIP_URI` in `.env` (SIP URI is the hostname from LiveKit Cloud, e.g. `your-project-id.sip.livekit.cloud` — no `sip:` prefix).
2. Run: `python scripts/setup_twilio_sip.py`
3. Run the agent (must stay running during the call):
   - **Phone testing:** `python agent.py start` (stable, no file-watcher restarts)
   - **Playground / dev:** `python agent.py dev`
4. Call your Twilio number — Priya should answer within 2–3 rings.

Do **not** run `test_worker_dispatch.py` while testing phone calls — it steals the worker with empty test rooms.

**Troubleshooting**

- **Call drops immediately:** Check the TwiML Bin URL is reachable and the SIP URI in the bin ends with `;transport=tcp`.
- **Agent doesn't answer:** Confirm `agent.py` is running and the worker `agent_name` matches the dispatch rule (`clinic-agent`). Run `python scripts/diagnose_telephony.py` — the dispatch rule must list `agents=['clinic-agent']`. A rule with only `room_prefix: call-` and no agents will connect the call but stay silent.
- **Audio cuts out:** Check Silero VAD downloaded successfully (`python agent.py download-files`).
- **Call drops mid-greeting:** `agent.py dev` restarts when files change (`DuplexClosed` in logs). Use `python agent.py start` for phone testing instead.

Docs: [Accepting inbound Twilio calls](https://docs.livekit.io/telephony/accepting-calls/inbound-twilio/)

---

## WhatsApp setup

After a successful booking, Priya sends a WhatsApp confirmation via Twilio in the background (the voice agent does not wait for delivery).

1. Go to [twilio.com/console/sms/whatsapp/sandbox](https://www.twilio.com/console/sms/whatsapp/sandbox)
2. Send `join <your-sandbox-keyword>` from the patient's WhatsApp to +1 415 523 8886
3. Set `TWILIO_WHATSAPP_FROM=whatsapp:+14155238886` in `.env`
4. The number you send **from** in code must match `TWILIO_WHATSAPP_FROM`
5. The number you send **to** must have joined the sandbox — for production, apply for a WhatsApp Business number via Twilio and remove the sandbox restriction

The sandbox requires each recipient number to opt in once. That is fine for testing, not for real patients — use a production WhatsApp Business number when you go live.

**How to test**

1. Quick test without a call: `python scripts/test_whatsapp_confirmation.py --phone <number>` (add `--dry-run` to preview only)
2. Or complete a test booking (phone call or Playground)
3. Priya should confirm the booking ID immediately with no extra pause
4. Within 5–10 seconds, WhatsApp should arrive on the patient's number
5. Check logs for the Twilio message SID (success) or an error line (failure)

---

## Project structure

```
reception-agent/
├── agent.py                  # Pipeline setup and LiveKit entrypoint
├── prompts/
│   └── system_prompt.py      # Clinic persona, FAQ knowledge, booking rules
├── tools/
│   ├── appointment.py        # Booking tools (book, check, list doctors)
│   └── notifications.py      # WhatsApp confirmation after booking
├── config.py                 # Env var loading and validation
├── scripts/
│   └── setup_twilio_sip.py   # One-time Twilio + LiveKit SIP setup
├── requirements.txt
├── .env.example
└── README.md
```

---
