"""
Test Google Calendar mirroring without running the voice agent.

Run from project root:
  python scripts/test_calendar.py status
  python scripts/test_calendar.py create --dry-run
  python scripts/test_calendar.py create --doctor "Dr. Sarah Lin"

Requires optional .env vars (see README → Google Calendar setup).
Does not reserve Supabase slots — only exercises create_calendar_event.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from tools.calendar_mirror import DOCTOR_CALENDAR_MAP, create_calendar_event  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test_calendar")

UTC = timezone.utc


def _default_iso() -> tuple[str, str]:
    tomorrow = datetime.now(UTC) + timedelta(days=1)
    return tomorrow.strftime("%Y-%m-%d"), "09:00"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Test Google Calendar mirror.")
    sub = parser.add_subparsers(dest="action", required=True)

    sub.add_parser("status", help="Show whether calendar mirroring is configured")

    create = sub.add_parser("create", help="Create a test event on a doctor's calendar")
    create.add_argument(
        "--doctor",
        default="Dr. Sarah Lin",
        choices=list(DOCTOR_CALENDAR_MAP.keys()),
    )
    create.add_argument("--name", default="Test Patient")
    create.add_argument("--phone", default="5550001234")
    create.add_argument("--date", default="Thursday 29 May", help="Spoken date (logging only)")
    create.add_argument("--time", default="nine in the morning", help="Spoken time (logging only)")
    create.add_argument("--reason", default="routine check-up (calendar test)")
    create.add_argument("--booking-id", default="TC-TEST-CAL")
    create.add_argument(
        "--iso-date",
        help="Event date YYYY-MM-DD (default: tomorrow UTC)",
    )
    create.add_argument(
        "--iso-time",
        help="Event time HH:MM 24h (default: 09:00)",
    )
    create.add_argument(
        "--dry-run",
        action="store_true",
        help="Print event details only; do not call Google Calendar API",
    )

    return parser.parse_args()


def _google_packages_ok() -> bool:
    try:
        import googleapiclient.discovery  # noqa: F401
        import google.oauth2.service_account  # noqa: F401

        return True
    except ImportError:
        return False


def _cmd_status() -> int:
    print("=== Calendar mirror status ===\n")
    enabled = config.calendar_enabled()
    print(f"calendar_enabled(): {enabled}")
    pkgs = _google_packages_ok()
    print(f"google-api-python-client installed: {pkgs}")
    if not pkgs:
        print("  Fix: pip install -r requirements.txt  (in your active venv)")
    print()

    creds = config.GOOGLE_CALENDAR_CREDENTIALS_JSON
    print(f"GOOGLE_CALENDAR_CREDENTIALS_JSON: {creds or '(not set)'}")
    if creds:
        path = Path(creds)
        if not path.is_absolute():
            path = Path.cwd() / path
        print(f"  file exists: {path.is_file()} ({path})")

    print(f"GOOGLE_CALENDAR_ID_SARAH: {config.GOOGLE_CALENDAR_ID_SARAH or '(not set)'}")
    print(f"GOOGLE_CALENDAR_ID_JAMES: {config.GOOGLE_CALENDAR_ID_JAMES or '(not set)'}")
    print()

    for doctor, cal_id in DOCTOR_CALENDAR_MAP.items():
        print(f"  {doctor} -> {cal_id or '(not set)'}")

    print()
    if enabled:
        print("Ready. Run: python scripts/test_calendar.py create")
    else:
        print("Not configured — agent will skip calendar mirroring (bookings still work).")
        print("Set the three env vars in .env (see README → Google Calendar setup).")
    return 0


def _preview_event(args: argparse.Namespace, iso_date: str, iso_time: str) -> None:
    cal_id = DOCTOR_CALENDAR_MAP.get(args.doctor, "(unknown)")
    start = datetime.strptime(f"{iso_date} {iso_time}", "%Y-%m-%d %H:%M").replace(tzinfo=UTC)
    end = start + timedelta(minutes=30)

    print("=== Event preview (dry run) ===")
    print(f"Calendar ID : {cal_id}")
    print(f"Summary     : [The Clinic] {args.name} — {args.reason}")
    print(f"Start (UTC) : {start.isoformat()}")
    print(f"End (UTC)   : {end.isoformat()}")
    print(f"Booking ref : {args.booking_id}")
    print(f"Patient     : {args.name} / {args.phone}")
    print()
    print("No API call made. Remove --dry-run to create the event.")


async def _cmd_create(args: argparse.Namespace) -> int:
    iso_date, iso_time = args.iso_date, args.iso_time
    if not iso_date or not iso_time:
        default_date, default_time = _default_iso()
        iso_date = iso_date or default_date
        iso_time = iso_time or default_time

    print("=== create_calendar_event ===\n")
    print(f"Doctor      : {args.doctor}")
    print(f"ISO start   : {iso_date} {iso_time} (UTC)")
    print(f"Booking ref : {args.booking_id}")
    print()

    if args.dry_run:
        _preview_event(args, iso_date, iso_time)
        return 0

    if not config.calendar_enabled():
        print("Calendar is not configured. Run: python scripts/test_calendar.py status")
        return 1

    ok = await create_calendar_event(
        patient_name=args.name,
        phone=args.phone,
        doctor=args.doctor,
        date=args.date,
        time=args.time,
        reason=args.reason,
        booking_id=args.booking_id,
        iso_date=iso_date,
        iso_time=iso_time,
    )
    if ok:
        print("Event created successfully.")
        return 0
    else:
        print("Event creation failed — check logs above.")
        return 1


def main() -> None:
    args = _parse_args()
    if args.action == "status":
        sys.exit(_cmd_status())
    elif args.action == "create":
        sys.exit(asyncio.run(_cmd_create(args)))


if __name__ == "__main__":
    main()
