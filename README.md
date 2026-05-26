# Reception Agent

An AI voice receptionist that picks up your phone line, books appointments, answers questions, and sends WhatsApp confirmations after every booking. Built for a medical clinic. Easy to adapt for any business that takes appointments over the phone.

```
Caller dials Twilio number
  -> TwiML Bin forwards to LiveKit SIP URI
    -> Inbound SIP trunk receives the call
      -> Dispatch rule routes to clinic-agent worker
        -> Deepgram (STT) -> Gemini (LLM) -> Murf (TTS)
```

**Stack**

| Layer | Service | Model / Version |
|---|---|---|
| Phone routing | Twilio + LiveKit SIP | |
| Speech to text | Deepgram | Nova-3 |
| Language model | Google AI | Gemini 2.0 Flash |
| Text to speech | Murf Falcon | `en-IN-anisha` |
| Voice activity detection | Silero | |
| Caller memory + slots | Supabase | |
| FAQ search | LanceDB + HuggingFace | all-MiniLM-L6-v2 |

---

## Contents

1. [Quick start](#1-quick-start)
2. [Telephony, WhatsApp, and call handoff](#2-telephony-whatsapp-and-call-handoff)
3. [Database, memory, and slots](#3-database-memory-and-slots)
4. [Google Calendar](#4-google-calendar)
5. [Transcript logging](#5-transcript-logging)
6. [Knowledge base (RAG)](#6-knowledge-base-rag)
7. [Project structure](#7-project-structure)
8. [Adapting for your use case](#8-adapting-for-your-use-case)
9. [Common errors](#9-common-errors)
10. [Resources](#10-resources)

---

## 1. Quick start

### Clone the repo

```bash
git clone <repo-url>
cd reception-agent
```

### Create a virtual environment

```bash
python -m venv venv
```

```bash
# macOS / Linux
source venv/bin/activate
```

```powershell
# Windows
venv\Scripts\Activate.ps1
```

### Install dependencies

```bash
pip install -r requirements.txt
```

### Configure environment variables

```bash
# macOS / Linux
cp .env.example .env
```

```powershell
# Windows
Copy-Item .env.example .env
```

Open `.env` and fill in the values below. You will come back to add telephony and database keys in later sections.

**Required**

| Variable | Where to get it |
|---|---|
| `LIVEKIT_URL` | [LiveKit Cloud](https://cloud.livekit.io) dashboard, your project |
| `LIVEKIT_API_KEY` | LiveKit Cloud > Settings > API Keys |
| `LIVEKIT_API_SECRET` | Same page as API key |
| `LIVEKIT_SIP_URI` | LiveKit Cloud > Telephony > SIP URI (hostname only, no `sip:` prefix) |
| `DEEPGRAM_API_KEY` | [console.deepgram.com](https://console.deepgram.com) |
| `GOOGLE_API_KEY` | [aistudio.google.com](https://aistudio.google.com) > API keys |
| `MURF_API_KEY` | murf.ai > Settings > API |
| `TWILIO_ACCOUNT_SID` | [console.twilio.com](https://console.twilio.com) > Account Info |
| `TWILIO_AUTH_TOKEN` | Same page as SID |
| `TWILIO_PHONE_NUMBER` | Your Twilio number in E.164 format (e.g. `+12015551234`) |
| `TWILIO_WHATSAPP_FROM` | `whatsapp:+14155238886` for sandbox testing |
| `SUPABASE_URL` | Supabase > Settings > API > Project URL |
| `SUPABASE_KEY` | Supabase > Settings > API > anon / public key |

**Optional** (agent works without these)

| Variable | What it enables |
|---|---|
| `CLINIC_PHONE_NUMBER` | Live call transfer to a real staff phone |
| `GOOGLE_CALENDAR_CREDENTIALS_JSON` | Mirror bookings to Google Calendar |
| `GOOGLE_CALENDAR_ID_MEERA` | Calendar ID for Dr. Meera Nair |
| `GOOGLE_CALENDAR_ID_ARUN` | Calendar ID for Dr. Arun Sharma |

### Download models

```bash
python agent.py download-files
```

Downloads Silero VAD and the FAQ embedding model (`all-MiniLM-L6-v2`, ~80 MB on first run), then builds the LanceDB index. You should see `FAQ index built: N chunks` in the output.

### Check all API keys

Run this before starting the agent for the first time. It tests connectivity to every service and prints a clear OK or FAIL for each one.

```bash
python scripts/check_apis.py
```

Fix any failures before continuing.

### Test in the browser (no phone needed)

```bash
python agent.py dev
```

Open the [LiveKit Agents Playground](https://agents-playground.livekit.io/), connect with your LiveKit URL, API key, and secret. The agent will join and you can talk to it with your microphone.

---

## 2. Telephony, WhatsApp, and call handoff

This section covers everything call-related: routing real phone calls to the agent, sending WhatsApp confirmations after bookings, and transferring calls to a human when asked.

### Step 1 - Twilio phone number

Log into [console.twilio.com](https://console.twilio.com). From the account dashboard, copy your **Account SID**, **Auth Token**, and **phone number** and add them to `.env`.

### Step 2 - LiveKit SIP trunk

The SIP trunk is the bridge that receives calls from Twilio and hands them to LiveKit.

1. Go to [LiveKit Cloud](https://cloud.livekit.io) > Telephony > SIP Trunks
2. Click **New trunk** and select **Inbound**
3. Fill in:
   - **Trunk name:** anything, e.g. `clinic-inbound`
   - **Numbers:** your Twilio number in E.164 format (e.g. `+12015551234`)
   - **Allowed addresses:** `0.0.0.0/0` to accept calls from all Twilio IPs
4. Save and copy the **SIP URI** shown at the top (e.g. `abc123.sip.livekit.cloud`)

Add it to `.env` as `LIVEKIT_SIP_URI` (hostname only, no `sip:` prefix).

### Step 3 - Twilio TwiML Bin

The TwiML Bin tells Twilio where to forward calls when your number receives one.

1. Go to Twilio Console > Develop > TwiML Bins > **Create new TwiML Bin**
2. Give it a name (e.g. `clinic`) and paste the following, replacing the SIP URI with yours:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Dial>
    <Sip>sip:YOUR_LIVEKIT_SIP_URI;transport=tcp</Sip>
  </Dial>
</Response>
```

Example: `sip:abc123.sip.livekit.cloud;transport=tcp`

3. Save. Confirm it shows **Valid Voice TwiML** at the bottom.
4. Go to **Phone Numbers > your number > Voice Configuration**
5. Set **Configure with** to **TwiML Bin** and select the bin you just created

### Step 4 - LiveKit dispatch rule

The dispatch rule tells LiveKit which agent worker to use when a call arrives.

1. Go to LiveKit Cloud > Telephony > Dispatch Rules > **Create new rule**
2. Fill in:
   - **Rule name:** `clinic-dispatch`
   - **Rule type:** Individual
   - **Room prefix:** `clinic-`
   - **Agent name:** `clinic-agent`
3. Switch to the **JSON editor** and confirm it looks like this:

```json
{
  "name": "clinic-dispatch",
  "rule": {
    "dispatchRuleIndividual": {
      "roomPrefix": "clinic-"
    }
  },
  "roomConfig": {
    "agents": [
      { "agentName": "clinic-agent" }
    ]
  }
}
```

The `agentName` must match exactly. A dispatch rule without the `agents` block will answer the call but stay completely silent.

### Step 5 - Run the agent and call it

Start the agent in a terminal. Use `start` for phone testing (stable, no file-watcher restarts):

```bash
python agent.py start
```

Call your Twilio number. The agent should answer within 2-3 rings.

**If calls arrive but the agent stays silent**, run the SIP monitor as a backup dispatcher in a second terminal. It polls every second and dispatches `clinic-agent` to any new SIP room that does not already have an agent:

```bash
python scripts/sip_monitor.py
```

To watch calls arrive and leave in real time:

```bash
python scripts/watch_calls.py
```

To verify the SIP trunk and dispatch rule are wired up correctly:

```bash
python scripts/diagnose_telephony.py
```

Alternatively, the setup script can create the SIP trunk and dispatch rule automatically from your `.env` values:

```bash
python scripts/setup_twilio_sip.py
```

### WhatsApp confirmations

After every booking, the agent sends a WhatsApp confirmation to the patient's number in the background. The voice call does not wait for delivery.

**Sandbox setup (free, for testing):**

1. Go to [Twilio Console > Messaging > Try WhatsApp](https://www.twilio.com/console/sms/whatsapp/sandbox)
2. From the patient's phone, send `join <keyword>` to `+1 415 523 8886`
3. Confirm `.env` has `TWILIO_WHATSAPP_FROM=whatsapp:+14155238886`

Each recipient must opt in once. For production, apply for a WhatsApp Business number through Twilio and update `TWILIO_WHATSAPP_FROM`.

**Test without making a call:**

```bash
python scripts/test_whatsapp_confirmation.py --phone +919876543210
python scripts/test_whatsapp_confirmation.py --phone +919876543210 --dry-run
```

Check logs for a Twilio message SID (success) or an error line (failure). The message should arrive within 5-10 seconds of a booking.

### Call handoff

When a caller asks to speak to a human, the agent transfers the live SIP call to a real phone via SIP REFER.

Add to `.env`:

```env
CLINIC_PHONE_NUMBER=+918041234567
```

Also enable **SIP REFER** in Twilio Console > Elastic SIP Trunking > your trunk > **General** tab > **Call Transfer (SIP REFER)** toggle. It is off by default.

If `CLINIC_PHONE_NUMBER` is not set, the agent reads the clinic number aloud and does not attempt a transfer. If `CLINIC_PHONE_NUMBER` is set but SIP REFER is not enabled, the transfer fails gracefully and the agent reads the number instead.

**Test:**

```bash
python scripts/test_handoff.py status
python scripts/test_handoff.py dry-run
python scripts/test_handoff.py messages
```

During a live call (get real room and identity values from the `rooms` command first):

```bash
python scripts/test_handoff.py rooms
python scripts/test_handoff.py refer --room clinic-XXXX --identity sip-XXXX
```

### Telephony troubleshooting

| Symptom | Fix |
|---|---|
| Call drops immediately | Confirm the TwiML Bin URL is reachable and the `<Sip>` URI ends with `;transport=tcp` |
| Agent does not answer | Run `python scripts/diagnose_telephony.py`. Confirm `agent.py` is running. Check the dispatch rule has `agentName: clinic-agent` in the `agents` block |
| Agent answers but stays silent | The dispatch rule is missing the `agents` block. Edit it in LiveKit Cloud. Also try `python scripts/sip_monitor.py` in a second terminal |
| Call drops mid-greeting | Use `python agent.py start` for phone testing. The `dev` mode restarts on every file save, which drops active calls |
| Audio cuts out | Run `python agent.py download-files` to re-verify the Silero VAD model |

---

## 3. Database, memory, and slots

All persistent data lives in Supabase: caller memory, appointment slots, booking records, and call transcripts.

### Set up the database

1. Create a free account at [supabase.com](https://supabase.com) (500 MB storage, no expiry)
2. Create a new project and wait for it to finish provisioning
3. Go to **Settings > API** and copy:
   - **Project URL** (e.g. `https://abcdefghijk.supabase.co`)
   - **anon / public key** (the longer of the two keys shown)
4. Add both to `.env` as `SUPABASE_URL` and `SUPABASE_KEY`
5. Go to **SQL Editor**, paste the full contents of `sql/create_tables.sql`, select all (Ctrl+A), and click **Run**

The script creates all four tables, adds indexes, and seeds 14 days of available slots so the agent can take bookings right away. It is safe to re-run.

### Tables

| Table | What it stores |
|---|---|
| `patients` | Caller name, preferred doctor, visit history, call count |
| `slots` | Every 30-minute slot: available or booked, with doctor, date, and time |
| `appointments` | Booking audit log with confirmed / cancelled / rescheduled status |
| `call_logs` | Full call transcript, intent label, and outcome label for every call |

### Caller memory

On every inbound call the agent looks up the caller's phone number from the SIP metadata. If a matching row is found in `patients`, their name and last booking details are injected into the system prompt before the call begins. The agent greets them by name and skips asking for their phone number again.

After a successful booking, `patients` and `appointments` are upserted automatically.

**Test memory without making a call:**

```bash
python scripts/test_memory.py lookup --phone 9876543210   # same lookup the agent runs
python scripts/test_memory.py call   --phone 9876543210   # simulate call start
python scripts/test_memory.py book   --phone 9876543210   # simulate post-booking write
python scripts/test_memory.py prompt --phone 9876543210   # preview the greeting
python scripts/test_memory.py flow   --phone 9876543210   # run all of the above in sequence
```

What to verify:
- **First call:** `patients` and `appointments` rows appear in Supabase after a booking
- **Second call (same number):** agent greets by name, `call_count` increments
- **Supabase down:** agent treats the caller as a first-timer (graceful fallback, no crash)

### Slot seeding

The initial seed in `sql/create_tables.sql` covers 14 days. An Edge Function keeps slots topped up automatically by running every Sunday at midnight IST.

**Deploy the Edge Function:**

1. Install the Supabase CLI:

```bash
npm install -g supabase
```

2. Link to your project. Your **project ref** is the subdomain in your project URL. For `https://abcdefghijk.supabase.co` the project ref is `abcdefghijk`. You can also find it at Settings > General > Reference ID.

```bash
supabase login
supabase link --project-ref YOUR_PROJECT_REF
```

3. Deploy the function from this repo:

```bash
supabase functions deploy seed-slots --no-verify-jwt
```

4. Enable `pg_cron` under **Database > Extensions**, then schedule the weekly run in the SQL editor. Replace `YOUR_PROJECT_REF` and `YOUR_SERVICE_ROLE_KEY` with real values.

Your **service role key** is at Settings > API > Project API keys > `service_role`. It has full database access so keep it out of version control and client code.

```sql
select cron.schedule(
  'seed-slots-weekly',
  '30 18 * * 0',   -- Sunday 18:30 UTC = midnight IST
  $$
  select net.http_post(
    url := 'https://YOUR_PROJECT_REF.supabase.co/functions/v1/seed-slots',
    headers := jsonb_build_object(
      'Content-Type', 'application/json',
      'Authorization', 'Bearer YOUR_SERVICE_ROLE_KEY'
    ),
    body := '{}'::jsonb
  )
  $$
);
```

**Test the Edge Function manually** before relying on the cron. Use your service role key from Settings > API.

macOS / Linux:

```bash
curl -sS -X POST "https://YOUR_PROJECT_REF.supabase.co/functions/v1/seed-slots" \
  -H "Authorization: Bearer YOUR_SERVICE_ROLE_KEY" \
  -H "Content-Type: application/json" \
  -d "{}"
```

Windows (PowerShell):

```powershell
curl.exe -sS -X POST "https://YOUR_PROJECT_REF.supabase.co/functions/v1/seed-slots" `
  -H "Authorization: Bearer YOUR_SERVICE_ROLE_KEY" `
  -H "Content-Type: application/json" `
  -d "{}"
```

Expected responses:

```json
{"message": "Seeded N slots from YYYY-MM-DD to YYYY-MM-DD"}
```

```json
{"message": "Slots OK - N days ahead. No seeding needed."}
```

Local test (requires Docker and `supabase start`):

```bash
supabase functions serve seed-slots --no-verify-jwt
# In a second terminal:
curl -sS -X POST "http://127.0.0.1:54321/functions/v1/seed-slots" \
  -H "Authorization: Bearer YOUR_SERVICE_ROLE_KEY" \
  -H "Content-Type: application/json" \
  -d "{}"
```

**Manual seed (fallback)** - run in the Supabase SQL editor if you need to fill slots immediately:

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

**Check slot coverage at any time:**

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

The agent logs `SLOT COVERAGE: N days ahead - OK` on startup. To test the warning, delete far-future slots and restart:

```sql
DELETE FROM slots WHERE iso_date > CURRENT_DATE + INTERVAL '10 days';
```

Run the manual seed above to restore.

**Verify the cron job:**

```sql
SELECT * FROM cron.job WHERE jobname = 'seed-slots-weekly';

SELECT * FROM cron.job_run_details
WHERE jobid = (SELECT jobid FROM cron.job WHERE jobname = 'seed-slots-weekly')
ORDER BY start_time DESC LIMIT 5;
```

---

## 4. Google Calendar

Supabase `slots` is the source of truth for availability. Google Calendar is a write-only mirror for clinic staff. The agent never reads from it.

1. Go to [console.cloud.google.com](https://console.cloud.google.com) and create a project
2. Enable the **Google Calendar API**
3. Go to **IAM > Service Accounts > Create** (no project-level role needed at this step)
4. Open the service account, go to **Keys > Add Key > JSON**, and save the file as `service-account.json` in the project root (already gitignored)
5. Open [Google Calendar](https://calendar.google.com) and create two calendars: one for each doctor
6. For each calendar: **Settings > Share with specific people > add the service account email > Make changes to events**
7. For each calendar: **Settings > Integrate calendar > Calendar ID** - copy the ID (e.g. `abc123@group.calendar.google.com`)
8. Add to `.env`:

```env
GOOGLE_CALENDAR_CREDENTIALS_JSON=./service-account.json
GOOGLE_CALENDAR_ID_MEERA=abc123@group.calendar.google.com
GOOGLE_CALENDAR_ID_ARUN=xyz456@group.calendar.google.com
```

**Test:**

```bash
python scripts/test_calendar.py status
python scripts/test_calendar.py create --dry-run
python scripts/test_calendar.py create --doctor "Dr. Meera Nair"
```

Without Calendar configured, logs show `Calendar mirror disabled - skipping` and bookings work normally. If credentials are misconfigured, the agent logs an error but the booking and WhatsApp confirmation still complete.

---

## 5. Transcript logging

Every call produces one row in `call_logs` (created by `sql/create_tables.sql`). The `transcript` column is a JSONB array of turns:

```json
[
  {"role": "agent", "text": "Hello, I'm Priya...", "ts": 0.0},
  {"role": "user",  "text": "Hi, I want to book an appointment", "ts": 3.2}
]
```

| Field | Values |
|---|---|
| `intent` | `booking`, `faq`, `cancellation`, `reschedule`, `unknown` |
| `call_outcome` | `booked`, `cancelled`, `rescheduled`, `answered_faq`, `transferred`, `abandoned`, `unknown` |

To view a transcript: Supabase dashboard > `call_logs` > expand the `transcript` cell. After a booking you should see `intent = booking` and `call_outcome = booked`.

**Privacy note:** Transcripts may contain caller names and visit reasons. Treat `call_logs` as sensitive personal data in production.

---

## 6. Knowledge base (RAG)

The agent answers factual questions by searching `knowledge/clinic_faq.md`. It does not guess. If the FAQ has no useful answer it says so and offers to have someone call the patient back.

**To update the knowledge base:**

1. Edit `knowledge/clinic_faq.md`
2. Restart the agent. The index rebuilds automatically on startup.

**Structure:** one `##` heading per topic. Each heading becomes one searchable chunk.

```markdown
## Clinic hours

We are open Monday to Saturday, 9 am to 1 pm and 5 pm to 8 pm.
Closed Sundays and public holidays.

## Consultation fees

A general consultation is 500 rupees. A follow-up within two weeks is 350 rupees.
```

**Index details:**
- Location: `.lancedb/` (gitignored, rebuilt on every startup)
- Embedding model: `all-MiniLM-L6-v2` from HuggingFace (~80 MB, downloaded once by `download-files`)
- Retrieval: top-3 nearest chunks by cosine similarity

More on the embedding model: [all-MiniLM-L6-v2 on HuggingFace](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2). More on LanceDB: [lancedb.github.io/lancedb](https://lancedb.github.io/lancedb/).

---

## 7. Project structure

```
reception-agent/
├── agent.py                       # LiveKit entrypoint, call routing, session setup
├── config.py                      # Env var loading and validation
├── knowledge/
│   └── clinic_faq.md              # FAQ knowledge base (one ## heading = one chunk)
├── prompts/
│   └── system_prompt.py           # Agent persona, booking rules, caller memory injection
├── tools/
│   ├── appointment.py             # book_appointment, check_availability, get_doctor_list
│   ├── booking.py                 # Supabase slot find + reserve
│   ├── calendar_mirror.py         # Google Calendar write-only mirror
│   ├── cancellation.py            # cancel_appointment, reschedule_appointment
│   ├── faq.py                     # LanceDB index + search_faq tool
│   ├── handoff.py                 # SIP REFER transfer to real phone
│   ├── memory.py                  # Patient lookup, upsert, call count
│   ├── notifications.py           # WhatsApp confirmation after booking
│   └── transcript.py              # Transcript collection and call_logs write
├── sql/
│   └── create_tables.sql          # Full Supabase schema - run this once to set up
├── supabase/
│   └── functions/
│       └── seed-slots/
│           └── index.ts           # Weekly slot-seeding Edge Function
├── scripts/
│   ├── check_apis.py              # Test all API keys before first run
│   ├── setup_twilio_sip.py        # One-time Twilio + LiveKit SIP setup
│   ├── sip_monitor.py             # Backup dispatcher for SIP rooms
│   ├── watch_calls.py             # Real-time call arrival monitor
│   ├── diagnose_telephony.py      # Check SIP trunk and dispatch rule config
│   ├── fix_sip_trunk.py           # Fix trunk phone number mismatch
│   ├── test_memory.py             # Test caller memory without a call
│   ├── test_whatsapp_confirmation.py
│   ├── test_calendar.py
│   └── test_handoff.py
├── requirements.txt
├── .env.example
└── README.md
```

---

## 8. Adapting for your use case

The clinic persona is a thin configuration layer. The call engine, slot system, memory, WhatsApp, and transcript logging are all business-agnostic. Here is what to change to make this a different kind of receptionist.

### What to change

| What | File | What to update |
|---|---|---|
| Agent name and persona | `prompts/system_prompt.py` | Identity block and opening line |
| Staff / provider names | `sql/create_tables.sql` | `slots_doctor_check` constraint |
| Booking flow (what to collect) | `prompts/system_prompt.py` | Booking intent section |
| Business hours and slot schedule | `sql/create_tables.sql` + `supabase/functions/seed-slots/index.ts` | Seed times and weekdays |
| FAQ content | `knowledge/clinic_faq.md` | Replace entirely, keep the `##` heading structure |
| Calendar names | `.env` | `GOOGLE_CALENDAR_ID_*` values |
| Handoff number | `.env` | `CLINIC_PHONE_NUMBER` |
| Voice | `agent.py` | Murf voice ID (`en-IN-anisha`), see [murf.ai/voices](https://murf.ai/voices) |

### Example: hair salon

**`prompts/system_prompt.py`**

```
You are Zara, the AI receptionist for Curl & Cut salon, Indiranagar, Bangalore.
Opening line: Hello, thanks for calling Curl & Cut. I'm Zara, your AI assistant. How can I help?
```

Update the booking intent section to collect: service type (haircut / colour / blowout), stylist preference, date, and time.

**`sql/create_tables.sql`** - update the constraint to your staff names:

```sql
constraint slots_stylist_check check (doctor in ('Aisha', 'Priya', 'Riya'))
```

Replace `knowledge/clinic_faq.md` with salon content: services, pricing, cancellation policy, directions.

### Example: legal intake

**`prompts/system_prompt.py`**

```
You are Alex, the AI intake assistant for Mehta & Associates.
Collect: caller name, contact number, matter type (civil / criminal / family / property),
and a brief description. Then schedule a callback with a solicitor.
```

For callback-only intake with no live slot booking, replace `book_appointment` with a lighter tool that logs the inquiry and records a preferred callback time. The rest of the pipeline (memory, transcripts, WhatsApp) still works as-is.

### Example: restaurant reservations

**`prompts/system_prompt.py`**

```
You are Anaya, reservations AI for The Spice Room.
Collect: guest name, contact number, date, time, party size, and dietary requirements.
```

**`sql/create_tables.sql`** - the `slots` table works naturally for table-time pairs. Use table names as the provider values:

```sql
constraint slots_table_check check (doctor in ('Table 1', 'Table 2', 'Table 3', 'Terrace'))
```

Update `supabase/functions/seed-slots/index.ts` for your opening hours, days of the week, and booking interval.

---

## 9. Common errors

| Error | Cause | Fix |
|---|---|---|
| `Required environment variable 'X' is not set` | Missing `.env` value | Copy `.env.example` to `.env` and fill in the variable |
| Agent answers but stays silent | Dispatch rule has no `agents` block | Edit the rule in LiveKit Cloud and add `agentName: clinic-agent` to the `roomConfig.agents` array |
| `DuplexClosed` in logs, call drops mid-greeting | `dev` mode restarts on file save | Use `python agent.py start` for all phone testing |
| Call drops immediately | TwiML Bin not reachable, or `<Sip>` URI missing `;transport=tcp` | Check the URI in the TwiML Bin and add `;transport=tcp` at the end |
| `ERROR: relation "slots" does not exist` | Ran only part of `create_tables.sql` | Select the entire file (Ctrl+A) and run it again from the top |
| `Table "slots" is missing` at seed time | Same as above | Same fix: run the whole file, not just the seed block at the bottom |
| `401` or `403` from Murf or Deepgram | Wrong or expired API key | Re-check `MURF_API_KEY` and `DEEPGRAM_API_KEY` in `.env` |
| WhatsApp message not delivered | Recipient has not joined the sandbox | Send `join <keyword>` from the recipient's WhatsApp to the sandbox number |
| Calendar events not appearing | Service account not shared with the calendar | Go to each calendar's settings and share it with the service account email with edit permissions |
| Slot coverage warning on startup | Fewer than 14 days of available slots | Run the manual seed SQL or POST to the Edge Function URL |

---

## 10. Resources

**Services used in this project**

- [LiveKit Cloud](https://cloud.livekit.io) - agent hosting and SIP telephony
- [LiveKit Agents Playground](https://agents-playground.livekit.io/) - browser-based testing, no phone needed
- [LiveKit docs: Accepting inbound Twilio calls](https://docs.livekit.io/telephony/accepting-calls/inbound-twilio/)
- [Deepgram console](https://console.deepgram.com) - speech to text API keys
- [Google AI Studio](https://aistudio.google.com) - Gemini API keys
- [Murf AI](https://murf.ai) - text to speech, [voice library](https://murf.ai/voices)
- [Twilio console](https://console.twilio.com) - phone numbers, TwiML Bins, SIP trunking
- [Twilio WhatsApp sandbox](https://www.twilio.com/console/sms/whatsapp/sandbox)
- [Supabase](https://supabase.com) - database, Edge Functions, pg_cron
- [Google Cloud Console](https://console.cloud.google.com) - service accounts for Calendar

**Libraries and models**

- [LanceDB](https://lancedb.github.io/lancedb/) - vector database for FAQ search
- [all-MiniLM-L6-v2](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2) - embedding model for FAQ indexing
- [Silero VAD](https://github.com/snakers4/silero-vad) - voice activity detection
