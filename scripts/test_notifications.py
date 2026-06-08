# scripts/test_notifications.py
"""
Verify Twilio WhatsApp delivery patterns in isolation.

Run from your workspace root:
  python scripts/test_notifications.py --phone YOUR_DEVICE_NUMBER
"""

import argparse
import asyncio
import sys
from pathlib import Path

# Append development routing structures
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.notifications import send_whatsapp_confirmation, notifications_enabled

async def main():
    parser = argparse.ArgumentParser(description="Test Twilio WhatsApp notification channels.")
    parser.add_argument(
        "--phone",
        type=str,
        required=True,
        help="Your sandbox verified phone number (e.g. 9843235177)"
    )
    args = parser.parse_args()

    print("=== Twilio WhatsApp Outbound Testing Loop ===")
    
    if not notifications_enabled():
        print("❌ Configuration verification failed.")
        print("Please audit your .env for TWILIO_ACCOUNT_SID, AUTH_TOKEN, and WHATSAPP_FROM values.")
        sys.exit(1)

    print(f"✅ Credentials matched. Dispatching text payload to targets...")

    success = await send_whatsapp_confirmation(
        phone=args.phone,
        patient_name="Rahul Sharma",
        doctor="Dr. Sarah Lin",
        date="Thursday 29 May",
        time="nine in the morning",
        booking_id="TC-TEST-WAB",
        reason="Outbound API Channel Audit"
    )

    if success:
        print("\n✅ API verification processing success!")
        print("Check your linked device for your transaction card layout stream.")
    else:
        print("\n❌ Event production dropped.")
        print("Review terminal connection exception logs for detailed trace diagnostic errors.")

if __name__ == "__main__":
    asyncio.run(main())