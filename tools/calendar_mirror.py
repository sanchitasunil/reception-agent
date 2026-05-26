"""
Google Calendar mirror for clinic staff visibility.
Write-only — the agent never reads from Calendar.
Fire-and-forget after successful Supabase booking.
If Calendar API is unavailable or misconfigured, booking is unaffected.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

import pytz

from config import (
    GOOGLE_CALENDAR_CREDENTIALS_JSON,
    GOOGLE_CALENDAR_ID_ARUN,
    GOOGLE_CALENDAR_ID_MEERA,
    calendar_enabled,
)

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")

DOCTOR_CALENDAR_MAP = {
    "Dr. Meera Nair": GOOGLE_CALENDAR_ID_MEERA,
    "Dr. Arun Sharma": GOOGLE_CALENDAR_ID_ARUN,
}


def _get_service():
    """
    Build and return an authenticated Google Calendar service object.
    Import google libs inside the function — they are optional dependencies.
    If import fails or credentials file is missing, raise so the caller
    can catch and skip.
    """
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise ImportError(
            "Google Calendar packages not installed. "
            "Run: pip install -r requirements.txt"
        ) from exc

    creds = service_account.Credentials.from_service_account_file(
        GOOGLE_CALENDAR_CREDENTIALS_JSON,
        scopes=["https://www.googleapis.com/auth/calendar"],
    )
    return build("calendar", "v3", credentials=creds)


async def create_calendar_event(
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
    Create a Google Calendar event on the doctor's clinic calendar.
    date and time are the spoken-form strings — used only for logging.
    iso_date ("2026-06-16") and iso_time ("09:00") are used for the actual event.

    Returns True on success, False on any failure. Never raises.
    """
    if not calendar_enabled():
        logger.debug("Calendar mirror disabled — skipping event creation")
        return False

    calendar_id = DOCTOR_CALENDAR_MAP.get(doctor)
    if not calendar_id:
        logger.warning("No calendar ID configured for doctor: %s", doctor)
        return False

    def _create() -> bool:
        try:
            service = _get_service()

            start_naive = datetime.strptime(f"{iso_date} {iso_time}", "%Y-%m-%d %H:%M")
            start = IST.localize(start_naive)
            end = start + timedelta(minutes=30)

            event = {
                "summary": f"[Arogya] {patient_name} — {reason}",
                "description": (
                    f"Patient: {patient_name}\n"
                    f"Phone: {phone}\n"
                    f"Reason: {reason}\n"
                    f"Booking ref: {booking_id}\n"
                    f"Booked via: Priya AI Receptionist"
                ),
                "start": {
                    "dateTime": start.isoformat(),
                    "timeZone": "Asia/Kolkata",
                },
                "end": {
                    "dateTime": end.isoformat(),
                    "timeZone": "Asia/Kolkata",
                },
                "colorId": "2",
                "reminders": {
                    "useDefault": False,
                    "overrides": [{"method": "popup", "minutes": 30}],
                },
            }

            result = (
                service.events()
                .insert(calendarId=calendar_id, body=event)
                .execute()
            )

            logger.info(
                "Calendar event created: %s for %s with %s on %s at %s",
                result.get("id"),
                patient_name,
                doctor,
                date,
                time,
            )
            return True

        except Exception as exc:
            logger.error("Calendar event creation failed: %s", exc)
            return False

    try:
        return await asyncio.to_thread(_create)
    except Exception as exc:
        logger.error("Calendar event creation failed: %s", exc)
        return False
