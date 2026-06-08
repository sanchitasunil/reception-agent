from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone

from supabase import Client, create_client

import config

logger = logging.getLogger(__name__)


_client: Client | None = None


def get_client() -> Client:
    global _client
    if _client is None:
        _client = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)
    return _client


_DIGIT_WORDS = [
    ("zero", "0"), ("one", "1"), ("two", "2"), ("three", "3"), ("four", "4"),
    ("five", "5"), ("six", "6"), ("seven", "7"), ("eight", "8"), ("nine", "9"),
]


def _words_to_digits(s: str) -> str:
    """Replace spoken digit words with numerals (handles spaced and concatenated forms)."""
    result = s
    for word, digit in sorted(_DIGIT_WORDS, key=lambda x: -len(x[0])):
        result = re.sub(word, digit, result, flags=re.IGNORECASE)
    return result


def _normalize_phone(phone: str) -> str:
    """Return E.164 +91XXXXXXXXXX (same rules as notifications, without whatsapp: prefix)."""
    cleaned = re.sub(r"[\s\-()]", "", _words_to_digits(phone).strip())

    if cleaned.startswith("+91"):
        return cleaned
    if cleaned.startswith("0"):
        return f"+91{cleaned[1:]}"
    if len(cleaned) == 10 and cleaned.isdigit():
        return f"+91{cleaned}"
    if cleaned.startswith("91") and len(cleaned) == 12 and cleaned.isdigit():
        return f"+{cleaned}"
    if cleaned.startswith("+"):
        return cleaned
    return f"+91{cleaned}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_patient_sync(phone: str) -> dict | None:
    client = get_client()
    normalized = _normalize_phone(phone)
    response = (
        client.table("customers")
        .select("*")
        .eq("phone", normalized)
        .limit(1)
        .execute()
    )
    if response.data:
        return response.data[0]
    return None


async def get_patient(phone: str) -> dict | None:
    """Look up a patient by normalised phone number. Returns the row dict or None."""
    try:
        return await asyncio.to_thread(_get_patient_sync, phone)
    except Exception as exc:
        logger.error("get_patient failed for %s: %s", phone, exc)
        return None


def _upsert_patient_sync(
    phone: str,
    name: str,
    doctor: str,
    booking_id: str,
    date: str,
    time: str,
) -> bool:
    client = get_client()
    normalized = _normalize_phone(phone)
    now = _now_iso()

    existing = (
        client.table("customers")
        .select("call_count")
        .eq("phone", normalized)
        .limit(1)
        .execute()
    )
    if existing.data:
        current = existing.data[0].get("call_count") or 0
        call_count = current + 1
    else:
        call_count = 1

    row = {
        "phone": normalized,
        "name": name,
        "preferred_doctor": doctor,
        "last_booking_id": booking_id,
        "last_appointment_date": date,
        "last_appointment_time": time,
        "call_count": call_count,
        "last_seen": now,
    }
    client.table("customers").upsert(row, on_conflict="phone").execute()
    return True


async def upsert_patient(
    phone: str,
    name: str,
    doctor: str,
    booking_id: str,
    date: str,
    time: str,
) -> bool:
    """Insert or update patient record after a successful booking."""
    try:
        return await asyncio.to_thread(
            _upsert_patient_sync, phone, name, doctor, booking_id, date, time
        )
    except Exception as exc:
        logger.error("upsert_patient failed for %s (booking %s): %s", phone, booking_id, exc)
        return False


def _log_appointment_sync(
    phone: str,
    doctor: str,
    date: str,
    time: str,
    reason: str,
    booking_id: str,
) -> bool:
    client = get_client()
    normalized = _normalize_phone(phone)
    row = {
        "id": booking_id,
        "phone": normalized,
        "doctor": doctor,
        "date": date,
        "time": time,
        "reason": reason,
        "booking_id": booking_id,
    }
    client.table("appointments").insert(row).execute()
    return True


async def log_appointment(
    phone: str,
    doctor: str,
    date: str,
    time: str,
    reason: str,
    booking_id: str,
) -> bool:
    """Insert a row into the appointments table. Returns True on success."""
    try:
        return await asyncio.to_thread(
            _log_appointment_sync, phone, doctor, date, time, reason, booking_id
        )
    except Exception as exc:
        logger.error(
            "log_appointment failed for %s (booking %s): %s", phone, booking_id, exc
        )
        return False


def _increment_call_count_sync(phone: str) -> None:
    client = get_client()
    normalized = _normalize_phone(phone)
    response = (
        client.table("customers")
        .select("call_count")
        .eq("phone", normalized)
        .limit(1)
        .execute()
    )
    if not response.data:
        return

    current = response.data[0].get("call_count") or 0
    client.table("customers").update(
        {"call_count": current + 1, "last_seen": _now_iso()}
    ).eq("phone", normalized).execute()


async def increment_call_count(phone: str) -> None:
    """Increment call_count and last_seen for an existing patient; no-op if unknown."""
    try:
        await asyncio.to_thread(_increment_call_count_sync, phone)
    except Exception as exc:
        logger.error("increment_call_count failed for %s: %s", phone, exc)
