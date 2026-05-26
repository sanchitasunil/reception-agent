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

### 5. Download models and build the FAQ index

```bash
python agent.py download-files
```

Downloads the Silero VAD model, the FAQ embedding model (`all-MiniLM-L6-v2`, ~80MB on first run), and builds the LanceDB index. You should see `FAQ index built: 12 chunks` (or similar) in the logs.

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

## Memory setup (Supabase)

Persistent caller memory lets Priya greet returning patients by name and reference their last appointment.

1. Go to [supabase.com](https://supabase.com), create a free project (free tier: 500MB, no expiry)
2. In the SQL editor, run the schema below
3. Copy the project URL and anon key from Settings → API
4. Set `SUPABASE_URL` and `SUPABASE_KEY` in `.env`

```sql
CREATE TABLE patients (
    phone TEXT PRIMARY KEY,
    name TEXT,
    preferred_doctor TEXT,
    last_booking_id TEXT,
    last_appointment_date TEXT,
    last_appointment_time TEXT,
    call_count INTEGER DEFAULT 1,
    first_seen TIMESTAMPTZ DEFAULT NOW(),
    last_seen TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE appointments (
    id TEXT PRIMARY KEY,
    phone TEXT REFERENCES patients(phone),
    doctor TEXT,
    date TEXT,
    time TEXT,
    reason TEXT,
    booking_id TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

**How to test**

Quick test without a call:

```bash
python scripts/test_memory.py lookup --phone 9876543210
python scripts/test_memory.py book --phone 9876543210
python scripts/test_memory.py call --phone 9876543210
python scripts/test_memory.py prompt --phone 9876543210
python scripts/test_memory.py flow --phone 9876543210
```

First call (new number):

- Priya asks for your name as normal
- After booking, check Supabase dashboard → `patients` table → row should appear
- Check `appointments` table → row should appear with `booking_id`

Second call (same number):

- Priya should greet you by name immediately without asking
- She should not ask for your phone number
- `call_count` in `patients` should have incremented

Edge cases:

- Call without booking → `call_count` increments, no patient/appointment rows written
- Supabase is down → `get_patient` returns `None` → Priya behaves as first-time caller (graceful fallback)
- Caller phone not available in SIP metadata → `patient_memory` is `None` → normal flow

---

## Cancellation and rescheduling (schema)

Run in the Supabase SQL editor (in addition to patients/appointments/slots):

```sql
ALTER TABLE slots ADD COLUMN IF NOT EXISTS cancelled_at TIMESTAMPTZ;
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'confirmed';
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS rescheduled_from TEXT;
```

`rescheduled_from` on a new appointment row stores the previous `booking_id` after a reschedule.

Slots use `status` (`available` / `booked`), `iso_date`, `iso_time`, `booking_id`, `phone`, `patient_name`, and `reason` — aligned with `tools/booking.py`.

**How to test**

Cancellation: book via phone, call again, say you want to cancel, confirm phone, say yes — check `slots.status=available`, `appointments.status=cancelled`, WhatsApp cancellation.

Reschedule: book, call to reschedule, give new date/time, confirm — old slot freed, new slot booked, WhatsApp reschedule message.

---

## Slot seeding automation

Slots are auto-seeded by a Supabase Edge Function that runs every Sunday at midnight IST. It ensures at least 30 days of future slots are always available for both doctors.

The `slots` table uses `iso_date`, `iso_time`, and `status` (`available` / `booked`), with a unique constraint on `(doctor, iso_date, iso_time)` — see `sql/create_tables.sql`.

### Deploy the Edge Function

1. Install Supabase CLI:

   ```bash
   npm install -g supabase
   ```

2. Link to your project:

   ```bash
   supabase login
   supabase link --project-ref your-project-ref
   ```

3. Create the function (skip if `supabase/functions/seed-slots/` already exists in this repo):

   ```bash
   supabase functions new seed-slots
   ```

4. Use the function source from this repo: `supabase/functions/seed-slots/index.ts` (or replace the generated file with it). It queries the latest `iso_date`, and if fewer than 30 days of coverage remain, inserts `available` slots through today + 60 days (Mon–Sat, morning and evening blocks, both doctors). Upserts are idempotent via `onConflict: doctor,iso_date,iso_time` and `ignoreDuplicates: true`.

5. Deploy the function:

   ```bash
   supabase functions deploy seed-slots --no-verify-jwt
   ```

6. Set up the weekly cron schedule in Supabase Dashboard:

   → Database → Extensions → enable `pg_cron` if not already enabled  
   → Database → SQL Editor → run:

   ```sql
   select cron.schedule(
     'seed-slots-weekly',
     '30 18 * * 0',   -- Sunday 18:30 UTC = midnight IST (Sunday night)
     $$
     select net.http_post(
       url := 'https://your-project-ref.supabase.co/functions/v1/seed-slots',
       headers := jsonb_build_object(
         'Content-Type', 'application/json',
         'Authorization', 'Bearer ' || current_setting('app.service_role_key')
       ),
       body := '{}'::jsonb
     )
     $$
   );
   ```

   Replace `your-project-ref` with your actual project ref(Your Project ID from Settings -> General -> Project ID).  
   Replace the Authorization Bearer value with your service role key if `current_setting('app.service_role_key')` is not configured.

7. Test the function manually before relying on the cron.

   The Supabase CLI no longer has `functions invoke` (CLI v2+). Call the deployed URL with `curl` instead (use your **service role** key from Dashboard → Settings → API):

   **bash / macOS / Linux:**

   ```bash
   curl -sS -L -X POST "https://YOUR_PROJECT_REF.supabase.co/functions/v1/seed-slots" \
     -H "Authorization: Bearer YOUR_SERVICE_ROLE_KEY" \
     -H "Content-Type: application/json" \
     -d "{}"
   ```

   **PowerShell (Windows):**

   ```powershell
   curl.exe -sS -L -X POST "https://YOUR_PROJECT_REF.supabase.co/functions/v1/seed-slots" `
     -H "Authorization: Bearer YOUR_SERVICE_ROLE_KEY" `
     -H "Content-Type: application/json" `
     -d "{}"
   ```

   Replace `YOUR_PROJECT_REF` and `YOUR_SERVICE_ROLE_KEY`. You must deploy with `--no-verify-jwt` (step 5) so this works without a user JWT.

   **Local test** (requires Docker + `supabase start`):

   ```bash
   supabase functions serve seed-slots --no-verify-jwt
   ```

   In another terminal:

   ```bash
   curl -sS -L -X POST "http://127.0.0.1:54321/functions/v1/seed-slots" \
     -H "Authorization: Bearer YOUR_SERVICE_ROLE_KEY" \
     -H "Content-Type: application/json" \
     -d "{}"
   ```

   Expected response:

   ```json
   {"message": "Seeded N slots from YYYY-MM-DD to YYYY-MM-DD"}
   ```

   or if slots are already sufficient:

   ```json
   {"message": "Slots OK — N days ahead. No seeding needed."}
   ```

### Manual seeding (fallback)

If you need to seed immediately without the Edge Function:

```sql
INSERT INTO slots (doctor, iso_date, iso_time, status)
SELECT
    doctor,
    date_val::DATE,
    slot_start::TIME,
    'available'
FROM
    UNNEST(ARRAY['Dr. Meera Nair', 'Dr. Arun Sharma']) AS doctor,
    generate_series(
        CURRENT_DATE,
        CURRENT_DATE + INTERVAL '60 days',
        INTERVAL '1 day'
    ) AS date_val,
    UNNEST(ARRAY[
        '09:00','09:30','10:00','10:30','11:00','11:30','12:00','12:30',
        '17:00','17:30','18:00','18:30','19:00','19:30'
    ]) AS slot_start
WHERE EXTRACT(ISODOW FROM date_val::DATE) BETWEEN 1 AND 6
ON CONFLICT (doctor, iso_date, iso_time) DO NOTHING;
```

### Monitoring

Check current slot coverage at any time:

```sql
SELECT
    MIN(iso_date) AS earliest_slot,
    MAX(iso_date) AS latest_slot,
    COUNT(*) FILTER (WHERE status = 'available') AS available_slots,
    COUNT(*) FILTER (WHERE status = 'booked') AS booked_slots,
    MAX(iso_date)::date - CURRENT_DATE AS days_of_coverage
FROM slots
WHERE iso_date >= CURRENT_DATE;
```

### How to test

**Coverage check at startup**

1. Run: `python agent.py dev`
2. Logs should show `SLOT COVERAGE: N days ahead — OK`
3. To test the warning, temporarily delete far-future slots:

   ```sql
   DELETE FROM slots WHERE iso_date > CURRENT_DATE + INTERVAL '10 days';
   ```

   Restart the agent — should log a critical warning. Re-run the manual seed SQL to restore.

**Edge Function**

1. Deploy: `supabase functions deploy seed-slots --no-verify-jwt`
2. POST to the function URL with `curl` (see step 7 above — not `supabase functions invoke`)
3. Check response JSON and run the monitoring query above

**Idempotency**

1. Run the `curl` POST twice back to back
2. Second run should report “Slots OK” or seed 0 new rows
3. No duplicate rows (unique constraint + `ignoreDuplicates`)

**Cron verification**

```sql
SELECT * FROM cron.job WHERE jobname = 'seed-slots-weekly';

SELECT * FROM cron.job_run_details
WHERE jobid = (SELECT jobid FROM cron.job WHERE jobname = 'seed-slots-weekly')
ORDER BY start_time DESC
LIMIT 5;
```

---

## Transcript logging setup

Every call is stored in Supabase `call_logs` when the room disconnects. The agent collects user and assistant speech during the call and writes one row per call.

Run this in the Supabase SQL editor:

```sql
CREATE TABLE call_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    phone TEXT,
    started_at TIMESTAMPTZ DEFAULT NOW(),
    ended_at TIMESTAMPTZ,
    duration_seconds INTEGER,
    transcript JSONB,
    booking_id TEXT,
    intent TEXT,
    call_outcome TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_call_logs_phone ON call_logs(phone);
CREATE INDEX idx_call_logs_started_at ON call_logs(started_at DESC);
```

The `transcript` column is JSONB — an array of turns:

```json
[
  {"role": "agent", "text": "Hello, I'm Priya...", "ts": 0.0},
  {"role": "user", "text": "Hi I want to book...", "ts": 3.2}
]
```

- **intent:** `booking`, `faq`, `cancellation`, `reschedule`, or `unknown` (inferred from keywords at call end if not set by a tool)
- **call_outcome:** `booked`, `cancelled`, `rescheduled`, `answered_faq`, `transferred`, `abandoned`, or `unknown` (set to `booked` when `book_appointment` succeeds)

**Privacy (DPDP):** `call_logs.phone` is stored in normalised `+91` E.164 form. Transcript text may include the patient's name and reason for visit — expected for clinic review, but treat `call_logs` as sensitive personal data in production.

**How to test**

1. Make a call (phone or Playground), ask a question, book if you can, then hang up
2. Open Supabase → `call_logs` — one new row
3. Expand `transcript` — alternating agent/user turns with relative `ts` (seconds from call start)
4. After a booking: `intent` = `booking`, `call_outcome` = `booked`, `booking_id` = `ARG-XXXX`
5. FAQ-only call (hours question, no booking): `intent` = `faq`, `call_outcome` = `unknown`

---

## Call handoff (SIP REFER)

When a patient asks for a human, Priya can cold-transfer the live SIP call to the clinic's real phone number via LiveKit SIP REFER.

Set in `.env`:

```env
CLINIC_PHONE_NUMBER=+918041234567
```

If unset, Priya gives the fallback number (080-41234567) and does not attempt a transfer.

**Twilio:** Enable SIP REFER on your Elastic SIP trunk — Console → Elastic SIP Trunking → your trunk → Call Transfer → **SIP REFER** (off by default). Without it, transfer fails gracefully and Priya reads the fallback number.

**How to test**

Quick test without the agent (config only):

```bash
python scripts/test_handoff.py status
python scripts/test_handoff.py dry-run
python scripts/test_handoff.py messages
```

During an active phone call (get real IDs — do not use `XXXX` placeholders):

```bash
python scripts/test_handoff.py rooms
python scripts/test_handoff.py refer --room clinic-<from-output> --identity <sip-from-output>
```

Without `CLINIC_PHONE_NUMBER`:

- Say "I want to speak to a doctor" — fallback number only, call stays with Priya

With `CLINIC_PHONE_NUMBER` (use your mobile for testing):

- Say "transfer me to a human" — Priya announces the transfer, your phone rings in a few seconds
- `call_logs.call_outcome` should be `transferred`

---

## Google Calendar setup

Supabase `slots` is the source of truth for availability. Google Calendar is a **write-only mirror** for clinic staff — the agent never reads from Calendar.

1. Go to [console.cloud.google.com](https://console.cloud.google.com) and create a project called "Arogya Clinic"
2. Enable the Google Calendar API for the project
3. Go to IAM → Service Accounts → Create service account  
   Name: `arogya-agent`, Role: none needed at project level
4. Create a JSON key for the service account and save as `service-account.json` in the project root (gitignored)
5. Open [Google Calendar](https://calendar.google.com)
6. Create two new calendars: **Dr. Meera Nair — Arogya** and **Dr. Arun Sharma — Arogya**
7. For each calendar: Settings → Share with specific people → add the service account email (e.g. `arogya-agent@your-project.iam.gserviceaccount.com`) → **Make changes to events**
8. Copy each calendar's ID from Settings → Integrate calendar → Calendar ID (e.g. `xxxxxxx@group.calendar.google.com`)
9. Set in `.env`:

```env
GOOGLE_CALENDAR_CREDENTIALS_JSON=./service-account.json
GOOGLE_CALENDAR_ID_MEERA=xxxxxxx@group.calendar.google.com
GOOGLE_CALENDAR_ID_ARUN=xxxxxxx@group.calendar.google.com
```

**How to test**

Without Calendar configured (default):

- Book an appointment normally
- Logs should show `Calendar mirror disabled — skipping`
- Booking, WhatsApp, and Supabase still complete

Quick test without a call:

```bash
python scripts/test_calendar.py status
python scripts/test_calendar.py create --dry-run
python scripts/test_calendar.py create --doctor "Dr. Meera Nair"
```

With Calendar configured:

1. Complete setup above and restart the agent
2. Book a test appointment
3. Within a few seconds, check the doctor's Google Calendar — event shows patient name, reason, and booking ref
4. Logs should show `Calendar event created: <event_id>`

Failure resilience:

- Set `GOOGLE_CALENDAR_CREDENTIALS_JSON` to a nonexistent path
- Book an appointment — calendar logs an error; Supabase booking and WhatsApp still succeed

---

## Knowledge base (RAG)

The agent answers FAQ questions by searching `knowledge/clinic_faq.md`.

To update clinic information:

- Edit `knowledge/clinic_faq.md`
- Restart the agent — the index rebuilds automatically on startup

To add a new topic: add a new `##` section to `clinic_faq.md`.
Each H2 section is one searchable entry.

Index location: `.lancedb/` (gitignored, auto-generated on startup)
Embedding model: `all-MiniLM-L6-v2` (~80MB, downloaded on first prewarm)
Run `python agent.py download-files` before first start to pre-download
both the Silero VAD model and the embedding model.

---

## Project structure

```
reception-agent/
├── agent.py                  # Pipeline setup and LiveKit entrypoint
├── knowledge/
│   └── clinic_faq.md         # Clinic knowledge (RAG source, chunked by H2)
├── prompts/
│   └── system_prompt.py      # Clinic persona, booking rules, RAG instructions
├── tools/
│   ├── appointment.py        # Booking tools (book, check, list doctors)
│   ├── booking.py            # Supabase slot find + reserve
│   ├── calendar_mirror.py    # Google Calendar write-only mirror
│   ├── cancellation.py       # cancel_appointment, reschedule_appointment
│   ├── handoff.py            # SIP REFER transfer to clinic phone
│   ├── faq.py                # LanceDB FAQ index + search_faq tool
│   ├── memory.py             # Supabase caller memory (lookup, upsert, log)
│   ├── notifications.py      # WhatsApp confirmation after booking
│   └── transcript.py         # Call transcript collection + call_logs write
├── config.py                 # Env var loading and validation
├── scripts/
│   └── setup_twilio_sip.py   # One-time Twilio + LiveKit SIP setup
├── requirements.txt
├── .env.example
└── README.md
```

---
