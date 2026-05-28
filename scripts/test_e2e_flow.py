"""
End-to-end flow test for reception-agent (no phone call required).

Exercises every functional layer against an in-memory database:
  1.  Phone normalization
  2.  Caller memory lookup + system prompt injection
  3.  Slot availability check
  4.  New booking  (+ async side-effects: WhatsApp, patient upsert, appointment log)
  5.  Doctor list tool
  6.  Cancellation — look-up step, then confirm step
  7.  Reschedule    — three-step: look-up -> propose -> confirm
  8.  Call transcript: add turns, infer intent, serialize to JSON
  9.  Transcript persistence  (mocked Supabase call_logs insert)
  10. WhatsApp message body formatting
  11. FAQ semantic search  (gracefully skipped when .lancedb index is absent)

Mocks: Supabase (in-memory FakeDB), Twilio (no HTTP calls), LiveKit (no agent context).
Google Calendar is automatically skipped because no calendar env vars are set.

Usage:
    python scripts/test_e2e_flow.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

# -- 1. Stub minimal env vars BEFORE importing config.py ---------------------
_FAKE_ENV: dict[str, str] = {
    "LIVEKIT_URL": "wss://test.livekit.cloud",
    "LIVEKIT_API_KEY": "test_key",
    "LIVEKIT_API_SECRET": "test_secret",
    "LIVEKIT_SIP_URI": "test.sip.livekit.cloud",
    "STT_PROVIDER": "deepgram",
    "DEEPGRAM_API_KEY": "test_deepgram",
    "LLM_PROVIDER": "openai",
    "OPENAI_API_KEY": "test_openai",
    "MURF_API_KEY": "test_murf",
    "TWILIO_ACCOUNT_SID": "ACtest000000000000000000000000001",
    "TWILIO_AUTH_TOKEN": "test_token_00000000000000000000001",
    "TWILIO_PHONE_NUMBER": "+12015551234",
    "TWILIO_WHATSAPP_FROM": "whatsapp:+14155238886",
    "SUPABASE_URL": "https://test.supabase.co",
    "SUPABASE_KEY": "test_supabase_key",
}
for _k, _v in _FAKE_ENV.items():
    os.environ.setdefault(_k, _v)

# -- 2. Stub livekit BEFORE importing any project module that decorates tools -


def _fake_function_tool(f=None, **kw):
    """Accept both @function_tool and @function_tool() usage."""
    if callable(f):
        return f
    return lambda fn: fn


_agents_mock = MagicMock()
_agents_mock.function_tool = _fake_function_tool
_agents_mock.get_job_context = MagicMock(return_value=None)


class _FakeRunContext:
    pass


_agents_mock.RunContext = _FakeRunContext
_agents_llm_mock = MagicMock()
_agents_llm_mock.function_tool = _fake_function_tool
_livekit_mock = MagicMock()
_livekit_mock.agents = _agents_mock

sys.modules.setdefault("livekit", _livekit_mock)
sys.modules.setdefault("livekit.agents", _agents_mock)
sys.modules.setdefault("livekit.agents.llm", _agents_llm_mock)

# -- 3. Project imports (now safe) --------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from prompts.system_prompt import build_system_prompt  # noqa: E402
from tools.appointment import book_appointment, check_availability, get_doctor_list  # noqa: E402
from tools.cancellation import (  # noqa: E402
    cancel_appointment,
    format_spoken_date,
    format_spoken_time,
    reschedule_appointment,
)
from tools.memory import _normalize_phone  # noqa: E402
from tools.notifications import _build_confirmation_body, _normalize_whatsapp_phone  # noqa: E402
from tools.transcript import (  # noqa: E402
    CallSession,
    infer_intent_from_turns,
    save_transcript,
)

# -- 4. In-memory Supabase replacement ----------------------------------------


class _FakeResult:
    def __init__(self, data: list) -> None:
        self.data = data


class _FakeTable:
    def __init__(self, rows: list) -> None:
        self._rows = rows
        self._op = "select"
        self._filters: list[tuple] = []
        self._order_keys: list[tuple] = []
        self._limit_n: int | None = None
        self._payload: Any = {}
        self._conflict_col: str = ""

    def select(self, cols: str = "*") -> "_FakeTable":
        # Don't change _op — select() just annotates return columns
        return self

    def insert(self, data: Any) -> "_FakeTable":
        self._op = "insert"
        self._payload = data
        return self

    def update(self, data: dict) -> "_FakeTable":
        self._op = "update"
        self._payload = data
        return self

    def upsert(self, data: dict, on_conflict: str = "") -> "_FakeTable":
        self._op = "upsert"
        self._payload = data
        self._conflict_col = on_conflict
        return self

    def eq(self, col: str, val: Any) -> "_FakeTable":
        self._filters.append(("eq", col, val))
        return self

    def gte(self, col: str, val: Any) -> "_FakeTable":
        self._filters.append(("gte", col, val))
        return self

    def order(self, col: str, desc: bool = False) -> "_FakeTable":
        self._order_keys.append((col, desc))
        return self

    def limit(self, n: int) -> "_FakeTable":
        self._limit_n = n
        return self

    def _matched_indices(self) -> list[int]:
        matched = []
        for i, row in enumerate(self._rows):
            ok = True
            for op, col, val in self._filters:
                if op == "eq" and row.get(col) != val:
                    ok = False
                    break
                if op == "gte" and str(row.get(col, "")) < str(val):
                    ok = False
                    break
            if ok:
                matched.append(i)
        return matched

    def execute(self) -> _FakeResult:
        if self._op == "select":
            indices = self._matched_indices()
            result = [self._rows[i] for i in indices]
            for col, desc in reversed(self._order_keys):
                result.sort(key=lambda r: str(r.get(col, "")), reverse=desc)
            if self._limit_n:
                result = result[: self._limit_n]
            return _FakeResult(result)

        if self._op == "insert":
            items = self._payload if isinstance(self._payload, list) else [self._payload]
            for row in items:
                self._rows.append(dict(row))
            return _FakeResult(items)

        if self._op == "update":
            indices = self._matched_indices()
            for i in indices:
                self._rows[i] = {**self._rows[i], **self._payload}
            return _FakeResult([self._rows[i] for i in indices])

        if self._op == "upsert":
            if self._conflict_col:
                cv = self._payload.get(self._conflict_col)
                for i, row in enumerate(self._rows):
                    if row.get(self._conflict_col) == cv:
                        self._rows[i] = {**row, **self._payload}
                        return _FakeResult([self._rows[i]])
            self._rows.append(dict(self._payload))
            return _FakeResult([self._payload])

        return _FakeResult([])


class FakeDB:
    def __init__(self, tables: dict[str, list]) -> None:
        self._tables = tables

    def table(self, name: str) -> _FakeTable:
        if name not in self._tables:
            self._tables[name] = []
        return _FakeTable(self._tables[name])


def _booking_db() -> FakeDB:
    """Fresh DB with one available slot (no patient yet)."""
    return FakeDB(
        {
            "slots": [
                {
                    "id": "slot-avail-001",
                    "doctor": "Dr. Sarah Lin",
                    "iso_date": "2026-06-05",
                    "iso_time": "09:00",
                    "status": "available",
                    "booking_id": None,
                    "patient_name": None,
                    "phone": None,
                    "reason": None,
                    "cancelled_at": None,
                }
            ],
            "patients": [],
            "appointments": [],
            "call_logs": [],
        }
    )


def _returning_patient_db() -> FakeDB:
    """Fresh DB with a booked appointment for a known patient."""
    return FakeDB(
        {
            "slots": [
                {
                    "id": "slot-booked-001",
                    "doctor": "Dr. James Cole",
                    "iso_date": "2026-06-10",
                    "iso_time": "10:00",
                    "status": "booked",
                    "booking_id": "ARG-1111",
                    "patient_name": "Rahul Sharma",
                    "phone": "+919876543210",
                    "reason": "blood pressure follow-up",
                    "cancelled_at": None,
                }
            ],
            "patients": [
                {
                    "phone": "+919876543210",
                    "name": "Rahul Sharma",
                    "preferred_doctor": "Dr. James Cole",
                    "last_booking_id": "ARG-1111",
                    "last_appointment_date": "2026-06-10",
                    "last_appointment_time": "10:00",
                    "call_count": 3,
                    "first_seen": "2026-01-01T00:00:00+00:00",
                    "last_seen": "2026-05-01T00:00:00+00:00",
                }
            ],
            "appointments": [
                {
                    "id": "ARG-1111",
                    "booking_id": "ARG-1111",
                    "phone": "+919876543210",
                    "doctor": "Dr. James Cole",
                    "date": "Wednesday the 10th of June",
                    "time": "10 in the morning",
                    "reason": "blood pressure follow-up",
                    "status": "confirmed",
                    "rescheduled_from": None,
                    "created_at": "2026-05-01T00:00:00+00:00",
                }
            ],
            "call_logs": [],
        }
    )


def _reschedule_db() -> FakeDB:
    """DB with booked appointment + a free slot on a different date to move into."""
    db = _returning_patient_db()
    db._tables["slots"].append(
        {
            "id": "slot-avail-002",
            "doctor": "Dr. James Cole",
            "iso_date": "2026-06-15",
            "iso_time": "09:00",
            "status": "available",
            "booking_id": None,
            "patient_name": None,
            "phone": None,
            "reason": None,
            "cancelled_at": None,
        }
    )
    return db


# -- 5. Test helpers ----------------------------------------------------------

_passed = 0
_failed = 0


def _check(label: str, condition: bool, detail: str = "") -> bool:
    global _passed, _failed
    if condition:
        print(f"  PASS  {label}")
        _passed += 1
    else:
        print(f"  FAIL  {label}" + (f" — {detail}" if detail else ""))
        _failed += 1
    return condition


async def _drain() -> None:
    """Let any background create_task coroutines complete."""
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    # One more pass for tasks spawned by the above
    for _ in range(3):
        await asyncio.sleep(0)


def _db_rows(db: FakeDB, table: str) -> list[dict]:
    return db._tables.get(table, [])


def _first(db: FakeDB, table: str, **eq_filters) -> dict | None:
    for row in db._tables.get(table, []):
        if all(row.get(k) == v for k, v in eq_filters.items()):
            return row
    return None


# -- 6. Individual test suites ------------------------------------------------


def test_phone_normalization() -> None:
    print("\n-- Phone normalization --")
    cases = [
        ("9876543210", "+919876543210"),
        ("09876543210", "+919876543210"),
        ("+919876543210", "+919876543210"),
        ("919876543210", "+919876543210"),
        ("+12125551234", "+12125551234"),  # non-Indian number left unchanged
    ]
    for raw, expected in cases:
        result = _normalize_phone(raw)
        _check(f"  {raw!r} -> {expected!r}", result == expected, f"got {result!r}")

    wa_cases = [
        ("9876543210", "whatsapp:+919876543210"),
        ("+919876543210", "whatsapp:+919876543210"),
        ("09876543210", "whatsapp:+919876543210"),
    ]
    for raw, expected in wa_cases:
        result = _normalize_whatsapp_phone(raw)
        _check(f"  WhatsApp {raw!r} -> {expected!r}", result == expected, f"got {result!r}")


def test_spoken_formatters() -> None:
    print("\n-- Spoken date/time formatters --")
    _check("1st suffix", format_spoken_date("2026-06-01") == "Monday the 1st of June")
    _check("2nd suffix", format_spoken_date("2026-06-02") == "Tuesday the 2nd of June")
    _check("3rd suffix", format_spoken_date("2026-06-03") == "Wednesday the 3rd of June")
    _check("4th suffix", format_spoken_date("2026-06-04") == "Thursday the 4th of June")
    _check("11th (no st)", format_spoken_date("2026-06-11") == "Thursday the 11th of June")
    _check("9:00 morning", format_spoken_time("09:00") == "9 in the morning")
    _check("12:00 afternoon", format_spoken_time("12:00") == "12 in the afternoon")
    _check("14:30 half-past", format_spoken_time("14:30") == "half past 2 in the afternoon")
    _check("18:00 evening", format_spoken_time("18:00") == "6 in the evening")


def test_system_prompt() -> None:
    print("\n-- System prompt injection --")
    prompt_new = build_system_prompt(None)
    _check(
        "new caller has no memory block",
        "Caller memory" not in prompt_new,
    )
    _check(
        "new caller prompt contains identity line",
        "I'm Aria" in prompt_new,
    )

    patient = {
        "name": "Rahul Sharma",
        "preferred_doctor": "Dr. James Cole",
        "last_booking_id": "ARG-1111",
        "last_appointment_date": "2026-06-10",
        "last_appointment_time": "10:00",
        "call_count": 3,
    }
    prompt_known = build_system_prompt(patient)
    _check("returning caller has memory block", "Caller memory" in prompt_known)
    _check("memory block contains name", "Rahul Sharma" in prompt_known)
    _check("memory block contains doctor", "Dr. James Cole" in prompt_known)
    _check("memory block contains booking ref", "ARG-1111" in prompt_known)
    _check(
        "memory block suppresses name prompt",
        'Do not say "May I know your name"' in prompt_known,
    )


async def test_availability_and_booking() -> None:
    print("\n-- Availability check + new booking --")
    db = _booking_db()
    mock_twilio = MagicMock()
    mock_twilio.return_value.messages.create.return_value.sid = "SMtest001"

    with (
        patch("tools.memory.get_client", return_value=db),
        patch("tools.booking.get_client", return_value=db),
        patch("tools.notifications.Client", mock_twilio),
    ):
        # check_availability
        avail = await check_availability(
            date="Friday 5 June", time="nine in the morning", doctor="Dr. Sarah Lin"
        )
        _check("slot is available", avail.get("available") is True)
        _check("slot_id returned", bool(avail.get("slot_id")))
        _check("iso_date returned", avail.get("iso_date") == "2026-06-05")

        # book_appointment
        result = await book_appointment(
            patient_name="Ananya Iyer",
            phone="9123456789",
            date="Friday 5 June",
            time="nine in the morning",
            doctor="Dr. Sarah Lin",
            reason="routine check-up",
            iso_date="2026-06-05",
            iso_time="09:00",
        )
        _check("booking confirmed", result.get("status") == "confirmed")
        bid = result.get("booking_id", "")
        _check("booking_id has ARG prefix", bid.startswith("ARG-"))

        # Allow background tasks (WhatsApp, patient upsert, appointment log) to run
        await _drain()

        slot = _first(db, "slots", id="slot-avail-001")
        _check("slot marked as booked", slot and slot.get("status") == "booked")
        _check("slot patient_name stored", slot and slot.get("patient_name") == "Ananya Iyer")

        patient = _first(db, "patients", phone="+919123456789")
        _check("patient upserted", patient is not None)
        _check("patient name stored", patient and patient.get("name") == "Ananya Iyer")
        _check(
            "patient preferred_doctor stored",
            patient and patient.get("preferred_doctor") == "Dr. Sarah Lin",
        )

        appt = _first(db, "appointments", booking_id=bid)
        _check("appointment logged", appt is not None)
        _check("appointment phone normalized", appt and appt.get("phone") == "+919123456789")

        _check(
            "WhatsApp send was called",
            mock_twilio.return_value.messages.create.called,
        )


async def test_doctor_list() -> None:
    print("\n-- Doctor list tool --")
    doctors = await get_doctor_list()
    _check("returns a list", isinstance(doctors, list))
    _check("two doctors returned", len(doctors) == 2)
    names = {d["name"] for d in doctors}
    _check("Dr. Sarah Lin present", "Dr. Sarah Lin" in names)
    _check("Dr. James Cole present", "Dr. James Cole" in names)
    for doc in doctors:
        _check(
            f"{doc['name']} has specialty",
            bool(doc.get("specialty")),
        )


async def test_cancellation() -> None:
    print("\n-- Cancellation flow --")
    db = _returning_patient_db()
    mock_twilio = MagicMock()
    mock_twilio.return_value.messages.create.return_value.sid = "SMcancel001"
    ctx = _FakeRunContext()

    with (
        patch("tools.memory.get_client", return_value=db),
        patch("tools.cancellation.get_client", return_value=db),
        patch("tools.cancellation.Client", mock_twilio),
    ):
        # Step 1: look-up (confirmed=False)
        resp1 = await cancel_appointment(
            phone="+919876543210", confirmed=False, context=ctx
        )
        _check(
            "step 1 reads back appointment",
            "Rahul Sharma" in resp1 and "ARG-1111" in resp1,
            resp1[:80],
        )
        _check("step 1 does NOT cancel yet", "cancelled" not in resp1.lower())

        slot_before = _first(db, "slots", id="slot-booked-001")
        _check("slot still booked before confirm", slot_before["status"] == "booked")

        # Step 2: confirm (confirmed=True)
        resp2 = await cancel_appointment(
            phone="+919876543210", confirmed=True, context=ctx
        )
        _check("step 2 confirms cancellation", "cancelled" in resp2.lower(), resp2[:80])

        await _drain()

        slot_after = _first(db, "slots", id="slot-booked-001")
        _check("slot freed after confirm", slot_after["status"] == "available")
        _check("slot phone cleared", slot_after.get("phone") is None)

        appt = _first(db, "appointments", id="ARG-1111")
        _check("appointment marked cancelled", appt and appt.get("status") == "cancelled")

        _check(
            "WhatsApp cancellation sent",
            mock_twilio.return_value.messages.create.called,
        )


async def test_reschedule() -> None:
    print("\n-- Reschedule flow --")
    db = _reschedule_db()
    mock_twilio = MagicMock()
    mock_twilio.return_value.messages.create.return_value.sid = "SMresched001"
    ctx = _FakeRunContext()

    with (
        patch("tools.memory.get_client", return_value=db),
        patch("tools.booking.get_client", return_value=db),
        patch("tools.cancellation.get_client", return_value=db),
        patch("tools.cancellation.Client", mock_twilio),
    ):
        # Step 1: look-up current appointment
        resp1 = await reschedule_appointment(
            phone="+919876543210",
            context=ctx,
            new_date="",
            new_time="",
            confirmed=False,
        )
        _check(
            "step 1 reads back current appointment",
            "Dr. James Cole" in resp1 and "ARG-1111" in resp1,
            resp1[:80],
        )

        # Step 2: propose new slot (confirmed=False)
        resp2 = await reschedule_appointment(
            phone="+919876543210",
            context=ctx,
            new_date="2026-06-15",
            new_time="09:00",
            confirmed=False,
        )
        _check(
            "step 2 proposes new slot",
            "reschedule" in resp2.lower() or "June" in resp2 or "09" in resp2,
            resp2[:80],
        )
        _check("step 2 does NOT reschedule yet", "Done" not in resp2)

        # Step 3: confirm reschedule
        resp3 = await reschedule_appointment(
            phone="+919876543210",
            context=ctx,
            new_date="2026-06-15",
            new_time="09:00",
            confirmed=True,
        )
        _check("step 3 confirms reschedule", "Done" in resp3 or "moved" in resp3, resp3[:80])

        await _drain()

        old_slot = _first(db, "slots", id="slot-booked-001")
        _check("old slot freed", old_slot["status"] == "available")

        new_slot = _first(db, "slots", id="slot-avail-002")
        _check("new slot booked", new_slot["status"] == "booked")
        _check("new slot patient set", new_slot.get("patient_name") == "Rahul Sharma")

        old_appt = _first(db, "appointments", id="ARG-1111")
        _check(
            "old appointment rescheduled",
            old_appt and old_appt.get("status") == "rescheduled",
        )

        new_bid = new_slot.get("booking_id", "")
        _check("new booking_id has ARG prefix", new_bid.startswith("ARG-"))

        _check(
            "WhatsApp reschedule notice sent",
            mock_twilio.return_value.messages.create.called,
        )


def test_call_session() -> None:
    print("\n-- Call session / transcript --")
    cs = CallSession(phone="+919876543210")

    cs.add_turn("agent", "Hello, this is Aria.")
    cs.add_turn("user", "I want to book an appointment.")
    cs.add_turn("agent", "Of course! May I know your name?")
    cs.add_turn("user", "My name is Rahul Sharma.")
    # Duplicate turn — should be ignored
    cs.add_turn("user", "My name is Rahul Sharma.")
    cs.add_turn("user", "  ")  # blank — should be ignored

    _check("four turns recorded (dupe + blank dropped)", len(cs.turns) == 4)
    _check("first turn is agent", cs.turns[0].role == "agent")
    _check("second turn is user", cs.turns[1].role == "user")
    _check("timestamps are non-negative", all(t.ts >= 0 for t in cs.turns))

    # Intent inference from keywords
    infer_intent_from_turns(cs)
    _check("booking intent inferred from 'book'", cs.intent == "booking")

    # Explicit outcome set
    cs.set_outcome(intent="booking", outcome="booked", booking_id="ARG-9999")
    _check("outcome set", cs.call_outcome == "booked")
    _check("booking_id stored", cs.booking_id == "ARG-9999")

    # Serialization
    tj = cs.to_transcript_json()
    _check("transcript JSON is a list", isinstance(tj, list))
    _check("each turn has role/text/ts", all({"role", "text", "ts"} <= set(t) for t in tj))
    _check("transcript length matches", len(tj) == 4)
    _check("duration non-negative", cs.duration_seconds() >= 0)

    # FAQ session
    cs_faq = CallSession(phone="+911111111111")
    cs_faq.add_turn("user", "What are your opening hours?")
    infer_intent_from_turns(cs_faq)
    _check("FAQ intent inferred (no booking keywords)", cs_faq.intent == "faq")

    # Cancellation
    cs_cancel = CallSession(phone="+912222222222")
    cs_cancel.add_turn("user", "I need to cancel, please.")
    infer_intent_from_turns(cs_cancel)
    _check("cancellation intent inferred", cs_cancel.intent == "cancellation")


async def test_save_transcript() -> None:
    print("\n-- Transcript persistence --")
    db = FakeDB({"call_logs": []})
    mock_create_client = MagicMock(return_value=db)

    cs = CallSession(phone="+919876543210")
    cs.add_turn("agent", "Hello, this is Aria.")
    cs.add_turn("user", "Book me an appointment.")
    cs.set_outcome(intent="booking", outcome="booked", booking_id="ARG-8888")

    with patch("tools.transcript.create_client", mock_create_client):
        ok = await save_transcript(cs)

    _check("save returns True", ok is True)
    logs = _db_rows(db, "call_logs")
    _check("one log row inserted", len(logs) == 1)
    row = logs[0]
    _check("log phone matches", row.get("phone") == "+919876543210")
    _check("log intent matches", row.get("intent") == "booking")
    _check("log outcome matches", row.get("call_outcome") == "booked")
    _check("log booking_id matches", row.get("booking_id") == "ARG-8888")
    _check("transcript JSON stored", isinstance(row.get("transcript"), list))
    _check("duration_seconds present", isinstance(row.get("duration_seconds"), int))


def test_whatsapp_body() -> None:
    print("\n-- WhatsApp message body --")
    body = _build_confirmation_body(
        patient_name="Ananya Iyer",
        doctor="Dr. Sarah Lin",
        date="Friday 5 June",
        time="nine in the morning",
        booking_id="ARG-5555",
        reason="routine check-up",
    )
    _check("body contains patient name", "Ananya Iyer" in body)
    _check("body contains doctor", "Dr. Sarah Lin" in body)
    _check("body contains date", "Friday 5 June" in body)
    _check("body contains booking ref", "ARG-5555" in body)
    _check("body contains clinic address", "Koramangala" in body)
    _check("body contains cancellation policy", "cancel" in body.lower())


async def test_faq_search() -> None:
    print("\n-- FAQ semantic search --")
    faq_db = Path(__file__).resolve().parent.parent / ".lancedb"
    if not faq_db.exists():
        print("  SKIP  .lancedb index not found — run 'python agent.py build-index' to generate it")
        return

    # Import only when index is present (heavy: loads SentenceTransformer)
    try:
        from tools.faq import search_faq  # noqa: PLC0415
    except Exception as exc:
        print(f"  SKIP  sentence_transformers/torch import failed: {exc}")
        return

    queries = [
        ("clinic hours query", "What are the clinic timings?"),
        ("fees query", "How much does a consultation cost?"),
        ("location query", "Where is the clinic located?"),
    ]
    for label, query in queries:
        result = await search_faq(query=query)
        _check(
            f"{label} returns non-empty result",
            bool(result) and "don't have specific information" not in result,
            result[:60] if result else "(empty)",
        )


# -- 7. Main runner -----------------------------------------------------------


async def _run_all() -> None:
    test_phone_normalization()
    test_spoken_formatters()
    test_system_prompt()
    await test_availability_and_booking()
    await test_doctor_list()
    await test_cancellation()
    await test_reschedule()
    test_call_session()
    await test_save_transcript()
    test_whatsapp_body()
    await test_faq_search()


def main() -> None:
    print("Reception-agent end-to-end flow test")
    print("=" * 42)
    t0 = time.perf_counter()
    asyncio.run(_run_all())
    elapsed = time.perf_counter() - t0
    total = _passed + _failed
    print()
    print("=" * 42)
    print(f"Results: {_passed}/{total} passed in {elapsed:.2f}s")
    if _failed:
        print(f"         {_failed} FAILED")
        sys.exit(1)
    else:
        print("         All tests passed.")


if __name__ == "__main__":
    main()
