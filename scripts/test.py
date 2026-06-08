# scripts/test_e2e.py
"""
Master End-to-End (E2E) Test Script.
Simulates the exact tool calls the AI makes, triggering all integrations
(Supabase, Google Calendar, and Twilio WhatsApp) simultaneously.

Run from project root:
  python scripts/test_e2e.py book --phone 9876543210
  python scripts/test_e2e.py cancel --phone 9876543210
  python scripts/test_e2e.py reschedule --phone 9876543210
"""

import argparse
import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

# Ensure project root is visible
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.appointment import check_availability, book_appointment
from tools.cancellation import cancel_appointment, reschedule_appointment

async def test_full_booking(phone: str):
    print("\n=== Simulating: Full AI Booking Flow ===")
    
    print("\n[Step 1] AI calls check_availability()...")
    avail = await check_availability(date="Tomorrow", time="morning", doctor="Dr. Sarah Lin")
    
    if not avail.get("available"):
        print("❌ No slots available in the database!")
        sys.exit(1)
        
    print(f"✅ Slot found: {avail['confirmed_slot']} (ISO: {avail['iso_date']} {avail['iso_time']})")
    
    print("\n[Step 2] AI calls book_appointment() after user confirms...")
    print("🚀 Firing Supabase, Google Calendar, and Twilio WhatsApp tasks in the background!")
    
    result = await book_appointment(
        patient_name="Rahul Sharma",
        phone=phone,
        date=avail["confirmed_slot"].split(" ")[0],
        time=avail["confirmed_slot"].split(" ")[1],
        doctor="Dr. Sarah Lin",
        reason="Full System E2E Test",
        iso_date=avail["iso_date"],
        iso_time=avail["iso_time"]
    )
    
    print(f"\n🤖 Aria says: \"{result.get('status', 'confirmed').title()}! Your appointment is locked.\"")
    
    print("\n⏳ Waiting 3 seconds to allow background network tasks to finish...")
    await asyncio.sleep(3)
    print("✅ Flow complete! Check your Google Calendar and WhatsApp.")

async def test_full_cancellation(phone: str):
    print("\n=== Simulating: Full AI Cancellation Flow ===")
    mock_ctx = MagicMock()

    print("\n[Step 1] AI looks up appointment (confirmed=False)...")
    readback = await cancel_appointment(phone=phone, confirmed=False, context=mock_ctx)
    print(f"🤖 Aria says: \"{readback}\"")

    if "I wasn't able to find" in readback:
        print("❌ No appointment found. Run the 'book' command first!")
        sys.exit(1)

    print("\n[Step 2] User says 'yes', AI executes deletion (confirmed=True)...")
    print("🚀 Firing Supabase updates and Twilio WhatsApp cancellation texts!")
    result = await cancel_appointment(phone=phone, confirmed=True, context=mock_ctx)
    print(f"🤖 Aria says: \"{result}\"")

    print("\n⏳ Waiting 3 seconds to allow background network tasks to finish...")
    await asyncio.sleep(3)
    print("✅ Flow complete! Check your database and WhatsApp.")

async def test_full_reschedule(phone: str):
    print("\n=== Simulating: Full AI Reschedule Flow ===")
    mock_ctx = MagicMock()

    print("\n[Step 1] AI looks up current appointment...")
    readback = await reschedule_appointment(phone=phone, context=mock_ctx)
    print(f"🤖 Aria says: \"{readback}\"")

    if "I wasn't able to find" in readback:
        print("❌ No appointment found. Run the 'book' command first!")
        sys.exit(1)

    print("\n[Step 2] AI proposes new time (confirmed=False)...")
    proposal = await reschedule_appointment(
        phone=phone, context=mock_ctx, new_date="2026-06-05", new_time="10:00", confirmed=False
    )
    print(f"🤖 Aria says: \"{proposal}\"")

    if "isn't available" in proposal:
        print("❌ Proposed slot is taken. Update test parameters.")
        sys.exit(1)

    print("\n[Step 3] User says 'yes', AI executes swap (confirmed=True)...")
    print("🚀 Firing Supabase race-condition locks, Google Calendar, and WhatsApp!")
    result = await reschedule_appointment(
        phone=phone, context=mock_ctx, new_date="2026-06-05", new_time="10:00", confirmed=True
    )
    print(f"🤖 Aria says: \"{result}\"")

    print("\n⏳ Waiting 3 seconds to allow background network tasks to finish...")
    await asyncio.sleep(3)
    print("✅ Flow complete! Check your database, Calendar, and WhatsApp.")

async def main():
    parser = argparse.ArgumentParser(description="Master E2E testing for all integrations.")
    sub = parser.add_subparsers(dest="action", required=True)

    book = sub.add_parser("book", help="Test the full booking flow (DB + Cal + WA)")
    book.add_argument("--phone", required=True)

    cancel = sub.add_parser("cancel", help="Test the full cancellation flow (DB + WA)")
    cancel.add_argument("--phone", required=True)

    reschedule = sub.add_parser("reschedule", help="Test the full reschedule flow (DB + WA + Cal)")
    reschedule.add_argument("--phone", required=True)

    args = parser.parse_args()

    if args.action == "book":
        await test_full_booking(args.phone)
    elif args.action == "cancel":
        await test_full_cancellation(args.phone)
    elif args.action == "reschedule":
        await test_full_reschedule(args.phone)

if __name__ == "__main__":
    asyncio.run(main())