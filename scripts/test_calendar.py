# scripts/test_calendar.py
"""
Exercise the Google Calendar integration without making a phone call.

Run from project root:
  python scripts/test_calendar.py --doctor "Dr. Sarah Lin"
  python scripts/test_calendar.py --doctor "Dr. James Cole"
"""

import argparse
import asyncio
import sys
from pathlib import Path
from datetime import date, timedelta

# Ensure we can import from the project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.calendar_mirror import create_calendar_event

async def main():
    parser = argparse.ArgumentParser(description="Test the Google Calendar integration.")
    parser.add_argument(
        "--doctor", 
        type=str, 
        default="Dr. Sarah Lin",
        help="The doctor to book the test appointment with (must match DOCTOR_CALENDAR_MAP)"
    )
    args = parser.parse_args()

    print("=== Google Calendar Integration Test ===")
    

    print(f"✅ Credentials found. Attempting to create an event for {args.doctor}...")

    # Schedule the test appointment for tomorrow at 10:00 AM
    tomorrow = date.today() + timedelta(days=1)
    iso_date = tomorrow.isoformat()
    iso_time = "10:00"

    # Fire the calendar tool
    success = await create_calendar_event(
        patient_name="Test Patient",
        phone="+919876543210",
        doctor=args.doctor,
        reason="API Integration Test",
        booking_id="TC-TEST-1234",
        date="Tomorrow",
        time="10:00 AM",
        iso_date=iso_date,
        iso_time=iso_time
    )

    if success:
        print("\n✅ Success!")
        print(f"Go check the Google Calendar for {args.doctor}.")
        print(f"You should see a new 30-minute event on {iso_date} at {iso_time}.")
    else:
        print("\n❌ Failed to create event.")
        print("Check your console logs above for the specific Google API error.")

if __name__ == "__main__":
    asyncio.run(main())