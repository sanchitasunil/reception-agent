"""
Send a test booking confirmation on WhatsApp (no agent call required).

Run from project root:
  python scripts/test_whatsapp_confirmation.py --phone 9876543210

Dry run (no Twilio API call):
  python scripts/test_whatsapp_confirmation.py --phone 9876543210 --dry-run

Requires .env with TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_WHATSAPP_FROM.
Recipient must have joined the Twilio WhatsApp sandbox if using the sandbox sender.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from tools.notifications import (  # noqa: E402
    _build_confirmation_body,
    _normalize_whatsapp_phone,
    send_whatsapp_confirmation,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test_whatsapp")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Test WhatsApp booking confirmation via Twilio.",
    )
    parser.add_argument(
        "--phone",
        required=True,
        help="Patient mobile (10 digits, 0-prefix, or +91). Must have joined sandbox.",
    )
    parser.add_argument("--name", default="Rahul Sharma", help="Patient name")
    parser.add_argument("--doctor", default="Dr. Meera Nair", help="Doctor name")
    parser.add_argument("--date", default="Thursday 29 May", help="Appointment date")
    parser.add_argument("--time", default="nine in the morning", help="Appointment time")
    parser.add_argument("--booking-id", default="ARG-4242", help="Booking reference")
    parser.add_argument(
        "--reason",
        default="follow-up on blood pressure",
        help="Reason for visit",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print normalized address and message only; do not call Twilio",
    )
    return parser.parse_args()


async def main() -> None:
    args = _parse_args()
    to = _normalize_whatsapp_phone(args.phone)

    print("=== Config ===")
    print(f"FROM : {config.TWILIO_WHATSAPP_FROM}")
    print(f"TO   : {to}")
    print()

    body = _build_confirmation_body(
        patient_name=args.name,
        doctor=args.doctor,
        date=args.date,
        time=args.time,
        booking_id=args.booking_id,
        reason=args.reason,
    )

    if args.dry_run:
        print("=== Message (dry run) ===")
        print(body)
        print()
        print("No message sent. Remove --dry-run to send via Twilio.")
        return

    print("=== Sending ===")
    ok = await send_whatsapp_confirmation(
        phone=args.phone,
        patient_name=args.name,
        doctor=args.doctor,
        date=args.date,
        time=args.time,
        booking_id=args.booking_id,
        reason=args.reason,
    )

    print()
    if ok:
        print("OK — check WhatsApp on the patient phone within a few seconds.")
        print("Also check logs above for the Twilio message SID.")
    else:
        print("FAILED — see error log above.")
        print("Common fixes:")
        print("  - TWILIO_WHATSAPP_FROM must be whatsapp:+14155238886 for sandbox")
        print("  - Recipient must send join <keyword> to +1 415 523 8886")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
