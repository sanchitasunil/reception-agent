from __future__ import annotations

import logging
import random
from typing import Any

from livekit.agents import function_tool

logger = logging.getLogger("clinic-agent.tools")


@function_tool
async def book_appointment(
    patient_name: str,
    phone: str,
    date: str,
    time: str,
    doctor: str,
    reason: str,
) -> dict[str, Any]:
    """
    Call this only after you have collected all booking details and the patient
    has explicitly confirmed yes to your readback summary.

    Use when the caller wants to book a new appointment and has agreed to proceed.
    Do not call without verbal confirmation first.
    """
    booking_id = f"ARG-{random.randint(1000, 9999)}"
    logger.info(f"Booking confirmed: {booking_id} for {patient_name}")
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
    logger.info(f"Checking availability: {doctor} on {date} at {time}")
    if random.random() < 0.2:
        return {
            "available": False,
            "next_available": f"{date} at ten in the morning",
        }
    return {
        "available": True,
        "confirmed_slot": f"{date} {time}",
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
            "name": "Dr. Meera Nair",
            "specialty": "General Physician",
            "experience": "15 years",
            "focus": "Diabetes and hypertension",
        },
        {
            "name": "Dr. Arun Sharma",
            "specialty": "Physician",
            "experience": "10 years",
            "focus": "Respiratory and infectious diseases",
        },
    ]
