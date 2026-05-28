"""
Supabase slot booking — source of truth for availability.
Google Calendar is mirrored separately after a successful reservation.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from typing import Any

from tools.memory import get_client

logger = logging.getLogger(__name__)

VALID_DOCTORS = ("Dr. Sarah Lin", "Dr. James Cole")


async def check_slot_coverage() -> None:
    """
    Called once at agent startup in prewarm.
    Queries how many days of future slots exist.
    Logs a warning if fewer than 14 days remain — gives the team
    time to intervene before slots run out.
    Never raises — slot coverage check must not block agent startup.
    """

    def _check() -> int:
        try:
            client = get_client()
            result = (
                client.table("slots")
                .select("iso_date")
                .gte("iso_date", date.today().isoformat())
                .eq("status", "available")
                .order("iso_date", desc=True)
                .limit(1)
                .execute()
            )
            if not result.data:
                return 0
            latest = date.fromisoformat(str(result.data[0]["iso_date"])[:10])
            return (latest - date.today()).days
        except Exception as exc:
            logger.error("Slot coverage check failed: %s", exc)
            return -1

    days = await asyncio.to_thread(_check)
    if days == -1:
        logger.error("Could not determine slot coverage — Supabase unreachable?")
    elif days == 0:
        logger.critical(
            "SLOT COVERAGE: NO future slots available — book_appointment will fail"
        )
    elif days < 7:
        logger.critical(
            "SLOT COVERAGE: Only %s days of slots remain — seed immediately", days
        )
    elif days < 14:
        logger.warning(
            "SLOT COVERAGE: %s days of slots remain — Edge Function may have missed a run",
            days,
        )
    else:
        logger.info("SLOT COVERAGE: %s days ahead — OK", days)


def _find_available_slot_sync(
    doctor: str,
    iso_date: str | None = None,
    iso_time: str | None = None,
) -> dict[str, Any]:
    client = get_client()
    query = (
        client.table("slots")
        .select("id, doctor, iso_date, iso_time, status")
        .eq("doctor", doctor)
        .eq("status", "available")
        .order("iso_date")
        .order("iso_time")
    )
    if iso_date:
        query = query.eq("iso_date", iso_date)
    if iso_time:
        query = query.eq("iso_time", iso_time)

    response = query.limit(1).execute()
    if not response.data:
        return {"available": False, "slot_id": None}

    row = response.data[0]
    return {
        "available": True,
        "slot_id": row["id"],
        "doctor": row["doctor"],
        "iso_date": row["iso_date"],
        "iso_time": _format_iso_time(row["iso_time"]),
    }


def _format_iso_time(value: str) -> str:
    """Normalize DB time to HH:MM for calendar and callers."""
    if not value:
        return value
    parts = str(value).split(":")
    if len(parts) >= 2:
        return f"{parts[0]}:{parts[1]}"
    return str(value)


def _spoken_from_iso(iso_date: str, iso_time: str) -> tuple[str, str]:
    """Best-effort spoken forms for check_availability responses."""
    try:
        dt = datetime.strptime(
            f"{iso_date} {_format_iso_time(iso_time)}", "%Y-%m-%d %H:%M"
        )
        return dt.strftime("%A the %d of %B"), dt.strftime("%I:%M %p").lstrip("0")
    except ValueError:
        return iso_date, iso_time


async def find_available_slot(
    doctor: str,
    iso_date: str | None = None,
    iso_time: str | None = None,
) -> dict[str, Any]:
    """
    Find the next available slot for a doctor.
    Returns available, slot_id, iso_date, iso_time, date, time (spoken forms).
    """
    if doctor not in VALID_DOCTORS:
        return {"available": False, "error": f"Unknown doctor: {doctor}"}

    try:
        result = await asyncio.to_thread(_find_available_slot_sync, doctor, iso_date, iso_time)
        if not result.get("available"):
            return {"available": False, "next_available": None}

        iso_d = result["iso_date"]
        iso_t = result["iso_time"]
        date_spoken, time_spoken = _spoken_from_iso(iso_d, iso_t)
        return {
            "available": True,
            "slot_id": result["slot_id"],
            "doctor": result["doctor"],
            "iso_date": iso_d,
            "iso_time": iso_t,
            "date": date_spoken,
            "time": time_spoken,
        }
    except Exception as exc:
        logger.error("find_available_slot failed: %s", exc)
        return {"available": False, "error": str(exc)}


def _reserve_slot_sync(
    slot_id: str,
    patient_name: str,
    phone: str,
    doctor: str,
    booking_id: str,
    reason: str,
) -> bool:
    client = get_client()
    response = (
        client.table("slots")
        .update(
            {
                "status": "booked",
                "booking_id": booking_id,
                "patient_name": patient_name,
                "phone": phone,
                "reason": reason,
            }
        )
        .eq("id", slot_id)
        .eq("doctor", doctor)
        .eq("status", "available")
        .select("id")
        .execute()
    )
    return bool(response.data)


async def reserve_slot(
    slot_id: str,
    patient_name: str,
    phone: str,
    doctor: str,
    date: str,
    time: str,
    reason: str,
    booking_id: str,
    iso_date: str,
    iso_time: str,
) -> bool:
    """
    Atomically reserve a slot in Supabase. Returns True only if the row was updated.
    Fires Google Calendar mirror on success (fire-and-forget).
    """
    try:
        reserved = await asyncio.to_thread(
            _reserve_slot_sync,
            slot_id,
            patient_name,
            phone,
            doctor,
            booking_id,
            reason,
        )
        if not reserved:
            logger.warning("reserve_slot failed — slot %s not available", slot_id)
            return False

        from tools.calendar_mirror import create_calendar_event

        asyncio.create_task(
            create_calendar_event(
                patient_name=patient_name,
                phone=phone,
                doctor=doctor,
                date=date,
                time=time,
                reason=reason,
                booking_id=booking_id,
                iso_date=iso_date,
                iso_time=iso_time,
            )
        )
        logger.info("Slot %s reserved for %s (%s)", slot_id, patient_name, booking_id)
        return True
    except Exception as exc:
        logger.error("reserve_slot failed for %s: %s", slot_id, exc)
        return False
