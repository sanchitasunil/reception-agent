from __future__ import annotations

import asyncio
import logging
import re

from twilio.rest import Client

import config

logger = logging.getLogger("clinic-agent.tools.notifications")


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


def _normalize_whatsapp_phone(phone: str) -> str:
    """Return Twilio WhatsApp address: whatsapp:+91XXXXXXXXXX."""
    cleaned = re.sub(r"[\s\-()]", "", _words_to_digits(phone).strip())

    if cleaned.startswith("+91"):
        e164 = cleaned
    elif cleaned.startswith("0"):
        e164 = f"+91{cleaned[1:]}"
    elif len(cleaned) == 10 and cleaned.isdigit():
        e164 = f"+91{cleaned}"
    elif cleaned.startswith("91") and len(cleaned) == 12 and cleaned.isdigit():
        e164 = f"+{cleaned}"
    elif cleaned.startswith("+"):
        e164 = cleaned
    else:
        e164 = f"+91{cleaned}"

    return f"whatsapp:{e164}"


def _build_confirmation_body(
    patient_name: str,
    doctor: str,
    date: str,
    time: str,
    booking_id: str,
    reason: str,
) -> str:
    return (
        f"Hi {patient_name}, your appointment at The Clinic is confirmed.\n"
        f"\n"
        f"Doctor: {doctor}\n"
        f"Date: {date}\n"
        f"Time: {time}\n"
        f"Ref: {booking_id}\n"
        f"Reason: {reason}\n"
        f"\n"
        f"📍 14 Birch Lane, Suite 2, Maplewood\n"
        f"\n"
        f"To cancel or reschedule, call us at 555-0142 at least 2 hours "
        f"before your appointment.\n"
        f"\n"
        f"— The Clinic"
    )


def _send_whatsapp_sync(to: str, body: str) -> str:
    client = Client(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)
    message = client.messages.create(
        body=body,
        from_=config.TWILIO_WHATSAPP_FROM,
        to=to,
    )
    return message.sid


async def send_whatsapp_confirmation(
    phone: str,
    patient_name: str,
    doctor: str,
    date: str,
    time: str,
    booking_id: str,
    reason: str,
) -> bool:
    """Send booking confirmation via Twilio WhatsApp. Never raises."""
    to = _normalize_whatsapp_phone(phone)
    body = _build_confirmation_body(
        patient_name=patient_name,
        doctor=doctor,
        date=date,
        time=time,
        booking_id=booking_id,
        reason=reason,
    )

    try:
        sid = await asyncio.to_thread(_send_whatsapp_sync, to, body)
        logger.info("WhatsApp confirmation sent to %s (SID %s)", to, sid)
        return True
    except Exception as exc:
        logger.error(
            "WhatsApp confirmation failed for %s (booking %s): %s",
            to,
            booking_id,
            exc,
        )
        return False
