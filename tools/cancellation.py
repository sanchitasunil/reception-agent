"""
Cancellation and rescheduling tools for The Clinic voice agent.
Both tools operate on the Supabase slots and appointments tables.
Cancellation frees a slot. Rescheduling cancels + rebooks atomically.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from datetime import datetime
from typing import Annotated, Any

from datetime import timezone

from livekit.agents import RunContext, function_tool, get_job_context
from pydantic import Field
from twilio.rest import Client

import config
from tools.booking import find_available_slot, reserve_slot
from tools.memory import _normalize_phone, get_client, log_appointment
from tools.transcript import get_call_session

logger = logging.getLogger(__name__)
UTC = timezone.utc

CLINIC_PHONE = "555-0142"


def normalise_doctor(doctor: str) -> str:
    """Match doctor name to canonical form."""
    d = doctor.strip()
    if "sarah" in d.lower() or "lin" in d.lower():
        return "Dr. Sarah Lin"
    if "james" in d.lower() or "cole" in d.lower():
        return "Dr. James Cole"
    return d


def format_spoken_date(iso_date: str) -> str:
    try:
        dt = datetime.strptime(iso_date[:10], "%Y-%m-%d")
        day = dt.day
        suffix = "th"
        if day % 10 == 1 and day != 11:
            suffix = "st"
        elif day % 10 == 2 and day != 12:
            suffix = "nd"
        elif day % 10 == 3 and day != 13:
            suffix = "rd"
        return f"{dt.strftime('%A')} the {day}{suffix} of {dt.strftime('%B')}"
    except ValueError:
        return iso_date


def format_spoken_time(iso_time: str) -> str:
    try:
        parts = str(iso_time).split(":")
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
        if hour == 0:
            h12, period = 12, "at night"
        elif hour < 12:
            h12, period = hour, "in the morning"
        elif hour == 12:
            h12, period = 12, "in the afternoon"
        elif hour < 17:
            h12, period = hour - 12, "in the afternoon"
        else:
            h12, period = hour - 12, "in the evening"
        if minute == 0:
            return f"{h12} {period}".replace("  ", " ")
        if minute == 30:
            return f"half past {h12} {period}"
        return f"{h12}:{minute:02d} {period}"
    except (ValueError, IndexError):
        return str(iso_time)


def _format_iso_time(value: str) -> str:
    parts = str(value).split(":")
    if len(parts) >= 2:
        return f"{parts[0]}:{parts[1]}"
    return str(value)


def _parse_preferred_datetime(new_date: str, new_time: str) -> tuple[str | None, str | None]:
    """Best-effort ISO date/time from patient phrases or ISO strings."""
    iso_date = None
    iso_time = None

    date_text = new_date.strip()
    time_text = new_time.strip()

    if re.match(r"^\d{4}-\d{2}-\d{2}$", date_text):
        iso_date = date_text
    if re.match(r"^\d{1,2}:\d{2}", time_text):
        iso_time = _format_iso_time(time_text)

    return iso_date, iso_time


def _lookup_upcoming_sync(phone: str) -> dict[str, Any] | None:
    client = get_client()
    normalized = _normalize_phone(phone)
    today = datetime.now(UTC).date().isoformat()

    slot_resp = (
        client.table("slots")
        .select("*")
        .eq("phone", normalized)
        .eq("status", "booked")
        .gte("iso_date", today)
        .order("iso_date")
        .order("iso_time")
        .limit(1)
        .execute()
    )
    if not slot_resp.data:
        return None

    slot = slot_resp.data[0]
    booking_id = slot.get("booking_id")
    if not booking_id:
        return None

    appt_resp = (
        client.table("appointments")
        .select("*")
        .eq("id", booking_id)
        .limit(1)
        .execute()
    )
    appt = appt_resp.data[0] if appt_resp.data else {}

    status = appt.get("status")
    if status and status not in ("confirmed",):
        return None

    iso_date = slot["iso_date"]
    iso_time = _format_iso_time(slot["iso_time"])
    patient_name = slot.get("patient_name") or appt.get("name") or "the patient"

    return {
        "booking_id": booking_id,
        "patient_name": patient_name,
        "phone": normalized,
        "doctor": normalise_doctor(slot.get("doctor") or appt.get("doctor", "")),
        "date": iso_date,
        "start_time": iso_time,
        "reason": slot.get("reason") or appt.get("reason") or "follow-up",
        "slot_id": slot["id"],
        "spoken_date": format_spoken_date(iso_date),
        "spoken_time": format_spoken_time(iso_time),
    }


async def lookup_upcoming_appointment(phone: str) -> dict[str, Any] | None:
    """Find the most recent upcoming confirmed appointment for a phone number."""
    try:
        return await asyncio.to_thread(_lookup_upcoming_sync, phone)
    except Exception as exc:
        logger.error("lookup_upcoming_appointment failed for %s: %s", phone, exc)
        return None


def _free_slot_sync(slot_id: str, booking_id: str) -> bool:
    client = get_client()
    now = datetime.now(UTC).isoformat()

    slot_resp = (
        client.table("slots")
        .update(
            {
                "status": "available",
                "booking_id": None,
                "patient_name": None,
                "phone": None,
                "reason": None,
                "cancelled_at": now,
            }
        )
        .eq("id", slot_id)
        .eq("status", "booked")
        .select("id")
        .execute()
    )
    if not slot_resp.data:
        return False

    client.table("appointments").update({"status": "cancelled"}).eq(
        "id", booking_id
    ).execute()
    return True


async def free_slot(slot_id: str, booking_id: str) -> bool:
    try:
        return await asyncio.to_thread(_free_slot_sync, slot_id, booking_id)
    except Exception as exc:
        logger.error("free_slot failed for slot %s: %s", slot_id, exc)
        return False


def _restore_slot_sync(slot_id: str, booking_id: str, appt: dict[str, Any]) -> bool:
    client = get_client()
    client.table("slots").update(
        {
            "status": "booked",
            "booking_id": booking_id,
            "patient_name": appt["patient_name"],
            "phone": appt["phone"],
            "reason": appt["reason"],
            "cancelled_at": None,
        }
    ).eq("id", slot_id).execute()
    return True


def _mark_rescheduled_sync(old_booking_id: str) -> None:
    client = get_client()
    client.table("appointments").update({"status": "rescheduled"}).eq(
        "id", old_booking_id
    ).execute()


def _normalize_whatsapp_phone(phone: str) -> str:
    cleaned = re.sub(r"[\s\-()]", "", phone.strip())
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


def _send_whatsapp_message_sync(to_phone: str, body: str) -> bool:
    client = Client(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)
    to = _normalize_whatsapp_phone(to_phone)
    message = client.messages.create(
        body=body,
        from_=config.TWILIO_WHATSAPP_FROM,
        to=to,
    )
    logger.info("WhatsApp sent to %s (SID %s)", to, message.sid)
    return True


async def send_whatsapp_cancellation(
    phone: str,
    patient_name: str,
    doctor: str,
    spoken_date: str,
    spoken_time: str,
    booking_id: str,
) -> bool:
    body = (
        f"Hi {patient_name}, your appointment at The Clinic has been cancelled.\n"
        f"\n"
        f"Cancelled appointment:\n"
        f"Doctor: {doctor}\n"
        f"Date: {spoken_date}\n"
        f"Time: {spoken_time}\n"
        f"Ref: {booking_id}\n"
        f"\n"
        f"To book a new appointment, call us on {CLINIC_PHONE} or speak to Aria.\n"
        f"We hope to see you soon.\n"
        f"\n"
        f"— The Clinic"
    )
    try:
        return await asyncio.to_thread(_send_whatsapp_message_sync, phone, body)
    except Exception as exc:
        logger.error("WhatsApp cancellation failed for %s: %s", phone, exc)
        return False


async def send_whatsapp_reschedule(
    phone: str,
    patient_name: str,
    doctor: str,
    old_spoken_date: str,
    old_spoken_time: str,
    new_spoken_date: str,
    new_spoken_time: str,
    new_booking_id: str,
) -> bool:
    body = (
        f"Hi {patient_name}, your appointment at The Clinic has been rescheduled.\n"
        f"\n"
        f"Previous appointment:\n"
        f"{old_spoken_date} at {old_spoken_time} with {doctor}\n"
        f"\n"
        f"New appointment:\n"
        f"Doctor: {doctor}\n"
        f"Date: {new_spoken_date}\n"
        f"Time: {new_spoken_time}\n"
        f"Ref: {new_booking_id}\n"
        f"\n"
        f"📍 14 Birch Lane, Suite 2, Maplewood\n"
        f"\n"
        f"To cancel, call {CLINIC_PHONE} at least 2 hours before your appointment.\n"
        f"\n"
        f"— The Clinic"
    )
    try:
        return await asyncio.to_thread(_send_whatsapp_message_sync, phone, body)
    except Exception as exc:
        logger.error("WhatsApp reschedule failed for %s: %s", phone, exc)
        return False


def _set_transcript_outcome(intent: str, outcome: str, booking_id: str | None = None) -> None:
    job_ctx = get_job_context(required=False)
    if not job_ctx:
        return
    cs = get_call_session(job_ctx.room.name)
    if cs:
        cs.set_outcome(intent=intent, outcome=outcome, booking_id=booking_id)


@function_tool()
async def cancel_appointment(
    phone: Annotated[str, Field(description="Patient's phone number as they provided it")],
    confirmed: Annotated[
        bool,
        Field(
            description=(
                "True only after patient explicitly confirmed cancellation. "
                "Never call with confirmed=True unless patient said yes."
            )
        ),
    ],
    context: RunContext,
) -> str:
    """
    Cancel a patient's upcoming appointment.
    Step 1: Call with confirmed=False to look up and read back the appointment.
    Step 2: After patient confirms, call again with confirmed=True to cancel.
    Never cancel without explicit patient confirmation — always do the two-step.
    phone: the patient's phone number.
    confirmed: False to look up, True to execute cancellation.
    """
    _ = context

    appt = await lookup_upcoming_appointment(phone)

    if not appt:
        return (
            "I wasn't able to find an upcoming appointment for that number. "
            "Could you double-check the number, or would you like to book a new appointment?"
        )

    if not confirmed:
        return (
            f"I found an appointment for {appt['patient_name']} "
            f"with {appt['doctor']} on {appt['spoken_date']} "
            f"at {appt['spoken_time']}, reference {appt['booking_id']}. "
            f"Shall I go ahead and cancel this appointment?"
        )

    success = await free_slot(appt["slot_id"], appt["booking_id"])

    if not success:
        return (
            "I wasn't able to cancel that appointment right now. "
            f"Please call us on {CLINIC_PHONE} and our team will sort it out."
        )

    asyncio.create_task(
        send_whatsapp_cancellation(
            phone=appt["phone"],
            patient_name=appt["patient_name"],
            doctor=appt["doctor"],
            spoken_date=appt["spoken_date"],
            spoken_time=appt["spoken_time"],
            booking_id=appt["booking_id"],
        )
    )
    _set_transcript_outcome("cancellation", "cancelled")

    return (
        f"Done — your appointment on {appt['spoken_date']} at "
        f"{appt['spoken_time']} has been cancelled. "
        f"You'll receive a WhatsApp confirmation shortly. "
        f"Is there anything else I can help you with?"
    )


@function_tool()
async def reschedule_appointment(
    phone: Annotated[str, Field(description="Patient's phone number")],
    context: RunContext,
    new_date: Annotated[str, Field(description="Preferred new date as patient said it")] = "",
    new_time: Annotated[str, Field(description="Preferred new time as patient said it")] = "",
    confirmed: Annotated[
        bool,
        Field(description="True only after patient confirmed the new slot"),
    ] = False,
) -> str:
    """
    Reschedule a patient's upcoming appointment to a new slot.
    Three-step flow:
    Step 1: Call with phone only (new_date="", new_time="", confirmed=False)
            → looks up current appointment and asks for new preferred time
    Step 2: Call with phone + new_date + new_time (confirmed=False)
            → finds available slot and reads it back for confirmation
    Step 3: Call with phone + new_date + new_time + confirmed=True
            → cancels old slot, books new slot, sends WhatsApp
    Never skip steps. Never set confirmed=True without explicit patient yes.
    """
    _ = context

    appt = await lookup_upcoming_appointment(phone)

    if not appt:
        return (
            "I wasn't able to find an upcoming appointment for that number. "
            "Could you double-check, or would you like to book a new appointment?"
        )

    if not new_date and not new_time:
        return (
            f"I found your appointment with {appt['doctor']} on "
            f"{appt['spoken_date']} at {appt['spoken_time']}, "
            f"reference {appt['booking_id']}. "
            f"What date and time would you prefer for the new appointment?"
        )

    iso_date, iso_time = _parse_preferred_datetime(new_date, new_time)
    new_slot = await find_available_slot(appt["doctor"], iso_date, iso_time)

    if not new_slot.get("available"):
        next_avail = new_slot.get("next_available") or "a different time"
        return (
            f"That slot isn't available. "
            f"The next available slot for {appt['doctor']} is {next_avail}. "
            f"Would that work for you?"
        )

    if not confirmed:
        return (
            f"I can reschedule your appointment to {new_slot['date']} "
            f"at {new_slot['time']} with {appt['doctor']}. "
            f"Shall I go ahead with that?"
        )

    new_booking_id = f"TC-{uuid.uuid4().hex[:6].upper()}"

    cancelled = await free_slot(appt["slot_id"], appt["booking_id"])
    if not cancelled:
        return (
            "I wasn't able to process the reschedule right now. "
            f"Please call us on {CLINIC_PHONE} and our team will help."
        )

    try:
        await asyncio.to_thread(_mark_rescheduled_sync, appt["booking_id"])
    except Exception as exc:
        logger.error("Failed to mark appointment rescheduled: %s", exc)

    booked = await reserve_slot(
        slot_id=new_slot["slot_id"],
        patient_name=appt["patient_name"],
        phone=appt["phone"],
        doctor=appt["doctor"],
        date=new_slot["date"],
        time=new_slot["time"],
        reason=appt["reason"],
        booking_id=new_booking_id,
        iso_date=new_slot["iso_date"],
        iso_time=new_slot["iso_time"],
    )

    if not booked:
        try:
            await asyncio.to_thread(_restore_slot_sync, appt["slot_id"], appt["booking_id"], appt)
            client = get_client()
            await asyncio.to_thread(
                lambda: client.table("appointments")
                .update({"status": "confirmed"})
                .eq("id", appt["booking_id"])
                .execute()
            )
        except Exception as exc:
            logger.error("Failed to restore slot after reschedule race: %s", exc)
        return (
            "That slot was just taken by someone else. "
            "Your original appointment is still in place. "
            "Would you like to try a different time?"
        )

    asyncio.create_task(
        log_appointment(
            appt["phone"],
            appt["doctor"],
            new_slot["date"],
            new_slot["time"],
            appt["reason"],
            new_booking_id,
        )
    )

    def _set_rescheduled_from() -> None:
        client = get_client()
        client.table("appointments").update(
            {"rescheduled_from": appt["booking_id"]}
        ).eq("id", new_booking_id).execute()

    asyncio.create_task(asyncio.to_thread(_set_rescheduled_from))

    asyncio.create_task(
        send_whatsapp_reschedule(
            phone=appt["phone"],
            patient_name=appt["patient_name"],
            doctor=appt["doctor"],
            old_spoken_date=appt["spoken_date"],
            old_spoken_time=appt["spoken_time"],
            new_spoken_date=new_slot["date"],
            new_spoken_time=new_slot["time"],
            new_booking_id=new_booking_id,
        )
    )
    _set_transcript_outcome("reschedule", "rescheduled", booking_id=new_booking_id)

    return (
        f"Done — your appointment has been moved to {new_slot['date']} "
        f"at {new_slot['time']} with {appt['doctor']}. "
        f"Your new reference is {new_booking_id}. "
        f"You'll get a WhatsApp confirmation shortly."
    )
