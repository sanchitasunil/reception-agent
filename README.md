# Reception Agent — AI Phone Receptionist

An AI voice agent that answers inbound calls, books appointments, handles FAQs, and sends WhatsApp confirmations. Shipped as a clinic receptionist (Arogya Clinic, Bangalore) but designed to be adapted for any appointment-based business.

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
```

```bash
# macOS / Linux
source venv/bin/activate
```

```powershell
# Windows (PowerShell)
venv\Scripts\Activate.ps1
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment variables

```bash
# macOS / Linux
cp .env.example .env
```

```powershell
# Windows (PowerShell)
Copy-Item .env.example .env
```

Open `.env` and fill in:

| Variable | Where to get it |
|---|---|
| `LIVEKIT_URL` | LiveKit Cloud dashboard → your project |
| `LIVEKIT_API_KEY` | LiveKit Cloud → Settings → API Keys |
| `LIVEKIT_API_SECRET` | Same as above |
| `LIVEKIT_SIP_URI` | LiveKit Cloud → Telephony → SIP URI (hostname only, no `sip:` prefix) |
| `DEEPGRAM_API_KEY` | console.deepgram.com |
| `GOOGLE_API_KEY` | aistudio.google.com → API keys |
| `MURF_API_KEY` | murf.ai → Settings → API |
| `TWILIO_ACCOUNT_SID` | console.twilio.com |
| `TWILIO_AUTH_TOKEN` | console.twilio.com |
| `TWILIO_PHONE_NUMBER` | Your Twilio phone number in E.164 format |
| `TWILIO_WHATSAPP_FROM` | Twilio WhatsApp sender (sandbox: `whatsapp:+14155238886`) |
| `SUPABASE_URL` | Supabase → Settings → API → Project URL |
| `SUPABASE_KEY` | Supabase → Settings → API → anon/public key |

> **Note on voice:** The agent uses `en-IN-anisha` (Murf Falcon). Check [murf.ai/voices](https://murf.ai/voices) for all available Falcon voice IDs and update `agent.py` if needed.

### 5. Download models and build the FAQ index

```bash
python agent.py download-files
```

Downloads the Silero VAD model and the FAQ embedding model (`all-MiniLM-L6-v2`, ~80 MB on first run), then builds the LanceDB index. You should see `FAQ index built: 12 chunks` (or similar) in the logs.

### 6. Run the agent in dev mode

```bash
python agent.py dev
```

The agent connects to the [LiveKit Agents Playground](https://agents-playground.livekit.io/) where you can test it with your microphone — no Twilio required at this stage.

---

## Database setup (Supabase)

All persistent state — caller memory, slots, bookings, and transcripts — lives in Supabase.

1. Go to [supabase.com](https://supabase.com) and create a free project (500 MB storage, no expiry)
2. Open `sql/create_tables.sql` from this repo, paste the entire file into the Supabase SQL editor, and run it (**Ctrl+A → Run** — do not run only a portion)
3. Copy the project URL and anon key from Settings → API and add them to `.env`

The script is safe to re-run — it uses `IF NOT EXISTS` and conditional blocks throughout. The seed block at the bottom inserts 14 days of available slots immediately so the agent can take bookings right away.

### Tables

| Table | Purpose |
|---|---|
| `patients` | Caller memory — name, preferred provider, visit history |
| `slots` | Appointment availability and bookings |
| `appointments` | Booking audit log with cancellation and reschedule tracking |
| `call_logs` | Full call transcripts with intent and outcome labels |

The `slots` table has a unique constraint on `(doctor, iso_date, iso_time)` and restricts `status` to `available` or `booked`. See `sql/create_tables.sql` for the full schema and indexes.

---

## Telephony setup (Twilio + LiveKit SIP)

Once the agent works in the Playground, connect a real phone number.

1. Fill in `TWILIO_*` and `LIVEKIT_SIP_URI` in `.env`
2. Run the one-time setup script:

   ```bash
   python scripts/setup_twilio_sip.py
   ```

3. Keep the agent running during calls:
   - **Phone testing:** `python agent.py start` (stable — no file-watcher restarts)
   - **Playground / dev:** `python agent.py dev`

4. Call your Twilio number — the agent should answer within 2–3 rings

Do **not** run `test_worker_dispatch.py` while testing phone calls — it steals the worker with empty test rooms.

**Troubleshooting**

- **Call drops immediately:** Check the TwiML Bin URL is reachable and the SIP URI in the bin ends with `;transport=tcp`
- **Agent doesn't answer:** Confirm `agent.py` is running and the `agent_name` matches the dispatch rule (`clinic-agent`). Run `python scripts/diagnose_telephony.py` — the dispatch rule must list `agents=['clinic-agent']`. A rule with only `room_prefix: call-` and no agents will connect the call but stay silent
- **Audio cuts out:** Run `python agent.py download-files` to re-verify the Silero VAD model
- **Call drops mid-greeting:** Use `python agent.py start` for phone testing — `dev` mode restarts on file changes (`DuplexClosed` in logs)

Docs: [Accepting inbound Twilio calls](https://docs.livekit.io/telephony/accepting-calls/inbound-twilio/)

---

## WhatsApp confirmations

After a successful booking, the agent sends a WhatsApp confirmation in the background — the voice call does not wait for delivery.

**Sandbox setup (testing)**

1. Go to [twilio.com/console/sms/whatsapp/sandbox](https://www.twilio.com/console/sms/whatsapp/sandbox)
2. Send `join <your-sandbox-keyword>` from the patient's WhatsApp to +1 415 523 8886
3. Set `TWILIO_WHATSAPP_FROM=whatsapp:+14155238886` in `.env`

Each recipient must opt in once. For production, apply for a WhatsApp Business number via Twilio and remove the sandbox restriction.

**How to test**

```bash
python scripts/test_whatsapp_confirmation.py --phone +919876543210
python scripts/test_whatsapp_confirmation.py --phone +919876543210 --dry-run  # preview only
```

Or complete a test booking — a WhatsApp confirmation should arrive within 5–10 seconds. Check logs for the Twilio message SID (success) or an error line (failure).

---

## Caller memory

Persistent memory lets the agent greet returning callers by name and skip collecting their phone number again.

**How it works**

On each call, the agent reads the caller's phone number from SIP metadata. If a matching row exists in `patients`, their name and last booking details are injected into the system prompt before the call begins. After a successful booking, `patients` and `appointments` are upserted.

**How to test**

```bash
python scripts/test_memory.py lookup --phone 9876543210
python scripts/test_memory.py book   --phone 9876543210
python scripts/test_memory.py call   --phone 9876543210
python scripts/test_memory.py prompt --phone 9876543210
python scripts/test_memory.py flow   --phone 9876543210
```

First call (new number):
- Agent asks for name as normal
- After booking, check Supabase → `patients` and `appointments` — rows should appear with `booking_id`

Second call (same number):
- Agent greets by name, skips the phone number question
- `call_count` in `patients` should have incremented

Edge cases:
- Call without booking → `call_count` increments, no `appointments` row written
- Supabase down → `get_patient` returns `None` → agent behaves as a first-time caller (graceful fallback)
- No SIP metadata → `patient_memory` is `None` → normal first-call flow

---

## Appointment management

### Cancellation

1. Agent collects the patient's phone number
2. Calls `cancel_appointment(phone=..., confirmed=False)` — looks up the appointment and reads it back
3. Asks "Shall I go ahead and cancel?" — waits for explicit yes
4. Calls `cancel_appointment(phone=..., confirmed=True)` — frees the slot, sends WhatsApp cancellation

### Rescheduling

1. Agent reads back the current booking
2. Collects preferred new date and time
3. Finds a slot and confirms — asks "Shall I go ahead with that?"
4. On yes: old slot freed, new slot booked, WhatsApp reschedule message sent

`rescheduled_from` on the new `appointments` row stores the previous `booking_id`.

**How to test**

Cancellation: book via phone or Playground, call again and say you want to cancel, confirm the number, say yes — check `slots.status = 'available'` and `appointments.status = 'cancelled'`.

Reschedule: book, call to reschedule, give a new date/time — check that the old slot was freed and a new slot is booked.

---

## Slot seeding

Slots are seeded by a Supabase Edge Function that runs every Sunday at midnight IST, ensuring at least 30 days of future availability.

### Deploy the Edge Function

1. Install the Supabase CLI:

   ```bash
   npm install -g supabase
   ```

2. Link to your project:

   ```bash
   supabase login
   supabase link --project-ref your-project-ref
   ```

3. Deploy from this repo (`supabase/functions/seed-slots/index.ts`):

   ```bash
   supabase functions deploy seed-slots --no-verify-jwt
   ```

4. Enable `pg_cron` under Supabase → Database → Extensions, then run this in the SQL editor to schedule the weekly job:

   ```sql
   select cron.schedule(
     'seed-slots-weekly',
     '30 18 * * 0',   -- Sunday 18:30 UTC = midnight IST
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

   Replace `your-project-ref` with your project ID (Settings → General → Project ID). If `current_setting('app.service_role_key')` is not configured, replace it with your service role key directly.

### Test the Edge Function

The Supabase CLI no longer has `functions invoke` (CLI v2+). Call the deployed URL with `curl` using your service role key from Dashboard → Settings → API.

**macOS / Linux:**

```bash
curl -sS -X POST "https://YOUR_PROJECT_REF.supabase.co/functions/v1/seed-slots" \
  -H "Authorization: Bearer YOUR_SERVICE_ROLE_KEY" \
  -H "Content-Type: application/json" \
  -d "{}"
```

**Windows (PowerShell):**

```powershell
curl.exe -sS -X POST "https://YOUR_PROJECT_REF.supabase.co/functions/v1/seed-slots" `
  -H "Authorization: Bearer YOUR_SERVICE_ROLE_KEY" `
  -H "Content-Type: application/json" `
  -d "{}"
```

Expected response:

```json
{"message": "Seeded N slots from YYYY-MM-DD to YYYY-MM-DD"}
```

Or if slots are already sufficient:

```json
{"message": "Slots OK — N days ahead. No seeding needed."}
```

**Local test** (requires Docker and `supabase start`):

```bash
supabase functions serve seed-slots --no-verify-jwt
# In a second terminal:
curl -sS -X POST "http://127.0.0.1:54321/functions/v1/seed-slots" \
  -H "Authorization: Bearer YOUR_SERVICE_ROLE_KEY" \
  -H "Content-Type: application/json" \
  -d "{}"
```

### Manual seeding (fallback)

Run this in the Supabase SQL editor to seed immediately without the Edge Function:

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

### Monitor slot coverage

```sql
SELECT
    MIN(iso_date)                                AS earliest_slot,
    MAX(iso_date)                                AS latest_slot,
    COUNT(*) FILTER (WHERE status = 'available') AS available_slots,
    COUNT(*) FILTER (WHERE status = 'booked')    AS booked_slots,
    MAX(iso_date)::date - CURRENT_DATE           AS days_of_coverage
FROM slots
WHERE iso_date >= CURRENT_DATE;
```

### Verify the cron job

```sql
SELECT * FROM cron.job WHERE jobname = 'seed-slots-weekly';

SELECT * FROM cron.job_run_details
WHERE jobid = (SELECT jobid FROM cron.job WHERE jobname = 'seed-slots-weekly')
ORDER BY start_time DESC LIMIT 5;
```

### Coverage check at startup

1. Run `python agent.py dev`
2. Logs should show `SLOT COVERAGE: N days ahead — OK`
3. To test the low-coverage warning, delete far-future slots and restart:

   ```sql
   DELETE FROM slots WHERE iso_date > CURRENT_DATE + INTERVAL '10 days';
   ```

   Re-run the manual seed SQL above to restore.

---

## Transcript logging

Every call is stored as one row in `call_logs` when the room disconnects. The `transcript` column is JSONB — an array of turns:

```json
[
  {"role": "agent", "text": "Hello, I'm Priya...", "ts": 0.0},
  {"role": "user",  "text": "Hi I want to book...", "ts": 3.2}
]
```

| Field | Values |
|---|---|
| `intent` | `booking`, `faq`, `cancellation`, `reschedule`, `unknown` |
| `call_outcome` | `booked`, `cancelled`, `rescheduled`, `answered_faq`, `transferred`, `abandoned`, `unknown` |

**Privacy (DPDP):** `call_logs.phone` is stored in normalised E.164 form. Transcript text may include the caller's name and reason for visit — treat `call_logs` as sensitive personal data in production.

**How to test**

1. Make a call (phone or Playground), ask a question, book if you can, then hang up
2. Open Supabase → `call_logs` — one new row
3. Expand `transcript` — alternating agent/user turns with relative `ts` (seconds from call start)
4. After a booking: `intent = booking`, `call_outcome = booked`, `booking_id = ARG-XXXX`
5. FAQ-only call: `intent = faq`, `call_outcome = unknown`

---

## Call handoff

When a caller asks for a human, the agent cold-transfers the live SIP call to the clinic's real phone via LiveKit SIP REFER.

Set in `.env`:

```env
CLINIC_PHONE_NUMBER=+918041234567
```

If unset, the agent reads the fallback number aloud and does not attempt a transfer.

**Twilio:** Enable SIP REFER on your Elastic SIP trunk — Console → Elastic SIP Trunking → your trunk → Call Transfer → **SIP REFER** (off by default). Without it, transfer fails gracefully and the agent reads the fallback number instead.

**How to test**

```bash
python scripts/test_handoff.py status
python scripts/test_handoff.py dry-run
python scripts/test_handoff.py messages
```

During an active phone call (use real IDs from `rooms` output — not placeholder values):

```bash
python scripts/test_handoff.py rooms
python scripts/test_handoff.py refer --room clinic-<from-output> --identity <sip-from-output>
```

---

## Google Calendar

Supabase `slots` is the source of truth for availability. Google Calendar is a **write-only mirror** for clinic staff — the agent never reads from Calendar.

1. Go to [console.cloud.google.com](https://console.cloud.google.com) and create a project
2. Enable the Google Calendar API
3. IAM → Service Accounts → Create service account (no project-level role needed)
4. Create a JSON key for the service account, save as `service-account.json` in the project root (gitignored)
5. In [Google Calendar](https://calendar.google.com), create two calendars: **Dr. Meera Nair — Arogya** and **Dr. Arun Sharma — Arogya**
6. For each calendar: Settings → Share with specific people → add the service account email → **Make changes to events**
7. Copy each calendar's ID from Settings → Integrate calendar → Calendar ID
8. Set in `.env`:

```env
GOOGLE_CALENDAR_CREDENTIALS_JSON=./service-account.json
GOOGLE_CALENDAR_ID_MEERA=xxxxxxx@group.calendar.google.com
GOOGLE_CALENDAR_ID_ARUN=xxxxxxx@group.calendar.google.com
```

**How to test**

Without Calendar configured (default):

```bash
python scripts/test_calendar.py status
python scripts/test_calendar.py create --dry-run
```

Logs show `Calendar mirror disabled — skipping`. Booking, WhatsApp, and Supabase still complete.

With Calendar configured:

```bash
python scripts/test_calendar.py create --doctor "Dr. Meera Nair"
```

Or complete a test booking — the event should appear in the doctor's calendar within a few seconds.

Failure resilience: point `GOOGLE_CALENDAR_CREDENTIALS_JSON` to a nonexistent path and book — Calendar logs an error but booking and WhatsApp still succeed.

---

## Knowledge base (RAG)

The agent answers FAQ questions by searching `knowledge/clinic_faq.md`.

- Edit `knowledge/clinic_faq.md` to update clinic information
- Restart the agent — the index rebuilds automatically on startup
- Add new topics by adding `##` sections; each H2 heading is one searchable chunk
- Index: `.lancedb/` (gitignored, auto-generated)
- Embedding model: `all-MiniLM-L6-v2` (~80 MB, downloaded on first `download-files`)

---

## Project structure

```
reception-agent/
├── agent.py                  # Pipeline setup and LiveKit entrypoint
├── knowledge/
│   └── clinic_faq.md         # Knowledge base (RAG source, chunked by H2)
├── prompts/
│   └── system_prompt.py      # Agent persona, booking rules, RAG instructions
├── tools/
│   ├── appointment.py        # Booking tools (book, check, list providers)
│   ├── booking.py            # Supabase slot find + reserve
│   ├── calendar_mirror.py    # Google Calendar write-only mirror
│   ├── cancellation.py       # cancel_appointment, reschedule_appointment
│   ├── handoff.py            # SIP REFER transfer
│   ├── faq.py                # LanceDB FAQ index + search_faq tool
│   ├── memory.py             # Supabase caller memory (lookup, upsert, log)
│   ├── notifications.py      # WhatsApp confirmation after booking
│   └── transcript.py         # Call transcript collection + call_logs write
├── sql/
│   └── create_tables.sql     # Full Supabase schema (run this once to set up)
├── supabase/
│   └── functions/
│       └── seed-slots/
│           └── index.ts      # Edge Function for weekly slot seeding
├── config.py                 # Env var loading and validation
├── scripts/                  # One-time setup and test utilities
├── requirements.txt
├── .env.example
└── README.md
```

---

## Adapting for your use case

The core engine — voice pipeline, slot management, caller memory, WhatsApp, transcripts — is business-agnostic. The clinic persona and rules are a thin layer on top. Here is what to change and three worked examples.

### What to change

| What to change | Where |
|---|---|
| Agent name and persona | `prompts/system_prompt.py` — identity block at the top |
| Opening line | Same file — hardcoded in the identity block |
| Provider / staff names | `sql/create_tables.sql` `slots_doctor_check` constraint + `prompts/system_prompt.py` booking instructions |
| Business hours and slot schedule | Seed block in `sql/create_tables.sql` + `supabase/functions/seed-slots/index.ts` |
| FAQ content | `knowledge/clinic_faq.md` — replace with your own, one `##` section per topic |
| Calendar names | `.env` / `.env.example` — `GOOGLE_CALENDAR_ID_*` variable names and values |
| Handoff number | `.env` → `CLINIC_PHONE_NUMBER` |
| Voice | `agent.py` → Murf voice ID (replace `en-IN-anisha` with your preferred voice) |

### Example: hair salon

**`prompts/system_prompt.py`** — update the identity block:

```
You are Zara, the AI receptionist for Curl & Cut salon, Indiranagar, Bangalore.
Your opening line: Hello, thanks for calling Curl & Cut. I'm Zara, your AI assistant. How can I help?
```

Update the booking flow to collect: service type (haircut / colour / blowout), stylist preference, date and time.

**`sql/create_tables.sql`** — change the `slots_doctor_check` constraint to your stylists:

```sql
constraint slots_stylist_check check (doctor in ('Aisha', 'Priya', 'Riya'))
```

**`knowledge/clinic_faq.md`** → replace with `knowledge/salon_faq.md` covering services, pricing, cancellation policy, parking.

**`.env`** — swap `GOOGLE_CALENDAR_ID_MEERA` / `GOOGLE_CALENDAR_ID_ARUN` for your stylists' calendar IDs.

### Example: legal intake

**`prompts/system_prompt.py`** — simplified intake persona (no slot booking needed):

```
You are Alex, the AI intake assistant for Mehta & Associates.
Collect: caller name, contact number, matter type (civil / criminal / family / property),
and a brief description. Then offer to schedule a callback with a solicitor.
```

For consultation slots, keep the booking engine as-is. For callback-only intake, replace `book_appointment` with a lighter tool that logs the inquiry and records a preferred callback time.

**`knowledge/clinic_faq.md`** → replace with `knowledge/firm_faq.md` — practice areas, fee structure, required documents, office location.

### Example: restaurant reservations

**`prompts/system_prompt.py`** — update persona and booking flow:

```
You are Anaya, reservations AI for The Spice Room.
Collect: guest name, phone, date, time, party size, and any dietary requirements.
```

**`sql/create_tables.sql`** — the `slots` table maps naturally to table-time pairs. Rename `doctor` to `table` conceptually and update the check constraint:

```sql
constraint slots_table_check check (doctor in ('Table 1', 'Table 2', 'Table 3', 'Terrace'))
```

**`supabase/functions/seed-slots/index.ts`** — update the seeding loop with your opening hours, days of the week, and booking interval (e.g. every 30 minutes from 12:00 to 22:00).
