# scripts/test_cancellation.py
"""
Verify the 2-step cancellation and 3-step reschedule flows.

Run from your workspace root:
  python scripts/test_cancellation.py cancel --phone 9876543210
  python scripts/test_cancellation.py reschedule --phone 9876543210
"""

import argparse
import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

# Ensure project root is visible
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.cancellation import cancel_appointment, reschedule_appointment

async def test_cancel_flow(phone: str):
    print("\n=== Simulating: 2-Step Cancellation Flow ===")
    mock_ctx = MagicMock()

    print("\n[Step 1] AI calls tool with confirmed=False to look up appointment...")
    readback = await cancel_appointment(phone=phone, confirmed=False, context=mock_ctx)
    print(f"🤖 Aria says: \"{readback}\"")

    if "I wasn't able to find" in readback:
        print("❌ No appointment found. Make sure to book one first using test_memory.py!")
        sys.exit(1)

    user_input = input("\n🗣️ You (Caller): Type 'yes' to confirm cancellation: ")
    
    if user_input.strip().lower() in ['y', 'yes']:
        print("\n[Step 2] AI calls tool with confirmed=True to execute deletion...")
        result = await cancel_appointment(phone=phone, confirmed=True, context=mock_ctx)
        print(f"🤖 Aria says: \"{result}\"")
        print("\n✅ Flow complete! Check your database and WhatsApp.")
    else:
        print("\n❌ Cancellation aborted by user.")

async def test_reschedule_flow(phone: str):
    print("\n=== Simulating: 3-Step Reschedule Flow ===")
    mock_ctx = MagicMock()

    print("\n[Step 1] AI calls tool with just the phone number to look up appointment...")
    readback = await reschedule_appointment(phone=phone, context=mock_ctx)
    print(f"🤖 Aria says: \"{readback}\"")

    if "I wasn't able to find" in readback:
        print("❌ No appointment found. Make sure to book one first using test_memory.py!")
        sys.exit(1)

    print("\n[Step 2] Providing new date/time (confirmed=False)...")
    new_date = input("📅 Enter new date (YYYY-MM-DD): ")
    new_time = input("⏰ Enter new time (HH:MM): ")

    proposal = await reschedule_appointment(
        phone=phone, context=mock_ctx, new_date=new_date, new_time=new_time, confirmed=False
    )
    print(f"\n🤖 Aria says: \"{proposal}\"")

    if "isn't available" in proposal:
        print("❌ Slot is taken or invalid. Try again.")
        sys.exit(1)

    user_input = input("\n🗣️ You (Caller): Type 'yes' to confirm reschedule: ")
    
    if user_input.strip().lower() in ['y', 'yes']:
        print("\n[Step 3] AI calls tool with confirmed=True to execute swap...")
        result = await reschedule_appointment(
            phone=phone, context=mock_ctx, new_date=new_date, new_time=new_time, confirmed=True
        )
        print(f"🤖 Aria says: \"{result}\"")
        print("\n✅ Flow complete! Check your database and WhatsApp.")
    else:
        print("\n❌ Reschedule aborted by user.")

async def main():
    parser = argparse.ArgumentParser(description="Test Cancellation and Rescheduling tools.")
    sub = parser.add_subparsers(dest="action", required=True)

    cancel = sub.add_parser("cancel", help="Test the cancellation flow")
    cancel.add_argument("--phone", required=True, help="Phone number with an existing appointment")

    reschedule = sub.add_parser("reschedule", help="Test the reschedule flow")
    reschedule.add_argument("--phone", required=True, help="Phone number with an existing appointment")

    args = parser.parse_args()

    if args.action == "cancel":
        await test_cancel_flow(args.phone)
    elif args.action == "reschedule":
        await test_reschedule_flow(args.phone)

if __name__ == "__main__":
    asyncio.run(main())