"""
Exercise Supabase caller memory without a phone call.

Run from project root:
  python scripts/test_memory.py lookup --phone 9876543210
  python scripts/test_memory.py book --phone 9876543210
  python scripts/test_memory.py call --phone 9876543210
  python scripts/test_memory.py prompt --phone 9876543210

Requires .env with SUPABASE_URL and SUPABASE_KEY. Tables must exist (see README).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from prompts.system_prompt import SYSTEM_PROMPT, build_system_prompt  # noqa: E402
from tools.memory import (  # noqa: E402
    _normalize_phone,
    get_patient,
    increment_call_count,
    log_appointment,
    upsert_patient,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test_memory")


def _print_patient(row: dict | None, normalized: str) -> None:
    print(f"Normalized phone: {normalized}")
    if row is None:
        print("Patient: (not found)")
        return
    print("Patient:")
    print(json.dumps(row, indent=2, default=str))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Test Supabase caller memory.")
    sub = parser.add_subparsers(dest="action", required=True)

    lookup = sub.add_parser("lookup", help="get_patient — same as agent on inbound call")
    lookup.add_argument("--phone", required=True)

    call = sub.add_parser("call", help="increment_call_count — simulates call start")
    call.add_argument("--phone", required=True)

    book = sub.add_parser(
        "book",
        help="upsert_patient + log_appointment — simulates after book_appointment",
    )
    book.add_argument("--phone", required=True)
    book.add_argument("--name", default="Rahul Sharma")
    book.add_argument("--doctor", default="Dr. Sarah Lin")
    book.add_argument("--date", default="Thursday 29 May")
    book.add_argument("--time", default="nine in the morning")
    book.add_argument(
        "--booking-id", 
        default=f"ARG-TEST-{uuid.uuid4().hex[:6].upper()}"
    )
    book.add_argument("--reason", default="follow-up on blood pressure")

    prompt = sub.add_parser(
        "prompt",
        help="lookup + print memory block appended to system prompt",
    )
    prompt.add_argument("--phone", required=True)

    flow = sub.add_parser(
        "flow",
        help="call → lookup → book → lookup (full debug sequence)",
    )
    flow.add_argument("--phone", required=True)
    flow.add_argument("--name", default="Test Patient")
    flow.add_argument("--doctor", default="Dr. Sarah Lin")
    flow.add_argument("--date", default="Friday 30 May")
    flow.add_argument("--time", default="ten in the morning")
    flow.add_argument(
        "--booking-id",
        default=f"ARG-TEST-{uuid.uuid4().hex[:6].upper()}"
    )
    flow.add_argument("--reason", default="routine check-up")

    delete = sub.add_parser("delete", help="remove patient + all appointments from Supabase")
    delete.add_argument("--phone", required=True)

    return parser.parse_args()


async def _cmd_lookup(phone: str) -> int:
    normalized = _normalize_phone(phone)
    print("=== lookup (get_patient) ===")
    print(f"SUPABASE_URL: {config.SUPABASE_URL}")
    print()
    row = await get_patient(phone)
    _print_patient(row, normalized)
    if row is None:
        print()
        print("No row (new caller or Supabase error — check logs).")
    return 0


async def _cmd_call(phone: str) -> int:
    normalized = _normalize_phone(phone)
    print("=== call (increment_call_count) ===")
    before = await get_patient(phone)
    _print_patient(before, normalized)
    print()
    await increment_call_count(phone)
    after = await get_patient(phone)
    print("After increment:")
    _print_patient(after, normalized)
    if before is None and after is None:
        print("(no-op — patient must exist; run book first)")
    return 0


async def _cmd_book(args: argparse.Namespace) -> int:
    normalized = _normalize_phone(args.phone)
    print("=== book (upsert_patient + log_appointment) ===")
    print(f"Normalized phone: {normalized}")
    print()

    ok_patient = await upsert_patient(
        args.phone,
        args.name,
        args.doctor,
        args.booking_id,
        args.date,
        args.time,
    )
    ok_appt = await log_appointment(
        args.phone,
        args.doctor,
        args.date,
        args.time,
        args.reason,
        args.booking_id,
    )

    print(f"upsert_patient: {'OK' if ok_patient else 'FAILED'}")
    print(f"log_appointment: {'OK' if ok_appt else 'FAILED'}")
    print()
    row = await get_patient(args.phone)
    _print_patient(row, normalized)

    if not (ok_patient and ok_appt):
        sys.exit(1)
    return 0


async def _cmd_prompt(phone: str) -> int:
    normalized = _normalize_phone(phone)
    print("=== prompt (build_system_prompt) ===")
    row = await get_patient(phone)
    _print_patient(row, normalized)
    print()
    if row is None:
        print("Using base SYSTEM_PROMPT only (no memory block).")
        print(f"Length: {len(SYSTEM_PROMPT)} chars")
        return 0

    memory_only = build_system_prompt(row)[len(SYSTEM_PROMPT) :]
    print("Memory block that would be appended:")
    print("-" * 40)
    print(memory_only.strip())
    print("-" * 40)
    example_name = row.get("name", "there")
    print()
    print("Example spoken greeting:")
    print(
        f'  "Hello {example_name}, welcome back to The Clinic. I\'m Aria, '
        f'your AI receptionist. How can I help you today?"'
    )
    return 0


async def _cmd_delete(phone: str) -> int:
    from supabase import create_client
    normalized = _normalize_phone(phone)
    print(f"=== delete ===")
    print(f"Normalized phone: {normalized}")
    client = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)
    client.table("appointments").delete().eq("phone", normalized).execute()
    client.table("patients").delete().eq("phone", normalized).execute()
    row = await get_patient(phone)
    if row is None:
        print("Deleted. Patient no longer found.")
    else:
        print("WARNING: patient row still present after delete.")
    return 0


async def _cmd_flow(args: argparse.Namespace) -> int:
    phone = args.phone
    print("=== flow (simulate: 2nd call + booking) ===\n")

    print("--- Step 1: seed patient (book) ---")
    await _cmd_book(args)

    print("\n--- Step 2: simulate inbound call (increment) ---")
    await _cmd_call(phone)

    print("\n--- Step 3: lookup (what agent sees) ---")
    await _cmd_lookup(phone)

    print("\n--- Step 4: prompt preview ---")
    await _cmd_prompt(phone)

    return 0


async def main() -> None:
    args = _parse_args()

    if args.action == "lookup":
        await _cmd_lookup(args.phone)
    elif args.action == "call":
        await _cmd_call(args.phone)
    elif args.action == "book":
        await _cmd_book(args)
    elif args.action == "prompt":
        await _cmd_prompt(args.phone)
    elif args.action == "flow":
        await _cmd_flow(args)
    elif args.action == "delete":
        await _cmd_delete(args.phone)


if __name__ == "__main__":
    asyncio.run(main())
