from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

from livekit.agents import function_tool, get_job_context

from tools.booking import find_available_slot, reserve_slot
from tools.transcript import get_call_session
from tools.memory import log_appointment, upsert_patient
from tools.notifications import send_whatsapp_confirmation

logger = logging.getLogger("clinic-agent.tools")


@function_tool
async def book_appointment(
    patient_name: str,
    phone: str,
    date: str,
    time: str,
    doctor: str,
    reason: str,
    iso_date: str | None = None,
    iso_time: str | None = None,
) -> dict[str, Any]:
    """
    Call this only after you have collected all booking details and the patient
    has explicitly confirmed yes to your readback summary.

    Use when the caller wants to book a new appointment and has agreed to proceed.
    Do not call without verbal confirmation first.

    iso_date and iso_time: pass the values returned by check_availability
    (format: "2026-05-29" and "15:00"). If omitted, the next available slot is used.
    """
    booking_id = f"TC-{random.randint(1000, 9999)}"

    slot = await find_available_slot(doctor, iso_date, iso_time)
    if not slot.get("available") or not slot.get("slot_id"):
        logger.warning("No available slot for %s", doctor)
        return {
            "status": "failed",
            "message": "No available slots for that doctor. Please try another day or doctor.",
        }

    reserved = await reserve_slot(
        slot_id=slot["slot_id"],
        patient_name=patient_name,
        phone=phone,
        doctor=doctor,
        date=date,
        time=time,
        reason=reason,
        booking_id=booking_id,
        iso_date=slot["iso_date"],
        iso_time=slot["iso_time"],
    )
    if not reserved:
        return {
            "status": "failed",
            "message": "That slot was just taken. Please try another time.",
        }

    logger.info("Booking confirmed: %s for %s", booking_id, patient_name)

    job_ctx = get_job_context(required=False)
    if job_ctx:
        cs = get_call_session(job_ctx.room.name)
        if cs:
            cs.set_outcome(
                intent="booking",
                outcome="booked",
                booking_id=booking_id,
            )

    asyncio.create_task(
        send_whatsapp_confirmation(
            phone=phone,
            patient_name=patient_name,
            doctor=doctor,
            date=date,
            time=time,
            booking_id=booking_id,
            reason=reason,
        )
    )
    asyncio.create_task(
        upsert_patient(phone, patient_name, doctor, booking_id, date, time)
    )
    asyncio.create_task(
        log_appointment(phone, doctor, date, time, reason, booking_id)
    )

    return {
        "patient_name": patient_name,
        "phone": phone,
        "date": date,
        "time": time,
        "doctor": doctor,
        "reason": reason,
        "booking_id": booking_id,
        "status": "confirmed",
    }


@function_tool
async def check_availability(
    date: str,
    time: str,
    doctor: str,
) -> dict[str, Any]:
    """
    Call this when the caller asks if a specific date, time, or doctor slot is
    available, before you commit to booking.

    Use after you know the preferred date, time, and doctor. If unavailable,
    offer the next_available slot from the tool result.
    """
    logger.info("Checking availability: %s on %s at %s", doctor, date, time)
    slot = await find_available_slot(doctor)
    if slot.get("available"):
        return {
            "available": True,
            "confirmed_slot": f"{slot.get('date', date)} {slot.get('time', time)}",
            "slot_id": slot.get("slot_id"),
            "iso_date": slot.get("iso_date"),
            "iso_time": slot.get("iso_time"),
        }
    return {
        "available": False,
        "next_available": slot.get("next_available"),
    }


@function_tool
async def get_doctor_list() -> list[dict[str, str]]:
    """
    Call this when the caller asks who the doctors are, what they specialise in,
    or wants help choosing between doctors.

    Use before describing doctors in detail or when the caller is unsure which
    doctor to see.
    """
    logger.info("get_doctor_list called")
    return [
        {
            "name": "Dr. Sarah Lin",
            "specialty": "General Physician",
            "experience": "15 years",
            "focus": "Diabetes and hypertension",
        },
        {
            "name": "Dr. James Cole",
            "specialty": "Physician",
            "experience": "10 years",
            "focus": "Respiratory and infectious diseases",
        },
    ]
