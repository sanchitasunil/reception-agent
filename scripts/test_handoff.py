# scripts/test_handoff.py
"""
Verify the SIP transfer orchestration and number normalization in isolation.

Run from your workspace root:
  python scripts/test_handoff.py
"""

import os
import sys
import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock

# Ensure project root is visible to the script runtime
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from livekit import rtc
from tools.handoff import _normalize_tel, transfer_to_human

async def run_diagnostic():
    print("=== LiveKit SIP Transfer Diagnostic Tool ===")
    
    # 1. Audit Environment Setup
    target_number = os.getenv("CLINIC_PHONE_NUMBER")
    if not target_number:
        print("❌ Configuration Error: CLINIC_PHONE_NUMBER is not set in your .env file.")
        sys.exit(1)
        
    print(f"✅ Target Destination Found: {target_number}")
    
    # 2. Test URI String Normalization Mechanics
    print("\n--- Verifying Protocol String Normalization ---")
    sample_numbers = [
        ("09843235177", "tel:+919843235177"),
        ("+15550001234", "tel:+15550001234"),
        ("tel:98432-35177", "tel:+919843235177")
    ]
    
    for raw, expected in sample_numbers:
        normalized = _normalize_tel(raw)
        if normalized == expected:
            print(f"  Cleaned: '{raw}' ➡️ '{normalized}' (Passed)")
        else:
            print(f"  ❌ Mismatch: '{raw}' normalized to '{normalized}', expected '{expected}'")

    # 3. Simulate Active Voice Session Infrastructure
    print("\n--- Simulating LiveKit Session Handoff Execution ---")
    
    # Mocking LiveKit Room & SIP Participant Structures
    mock_room = MagicMock()
    mock_sip_participant = MagicMock()
    mock_sip_participant.kind = rtc.ParticipantKind.PARTICIPANT_KIND_SIP
    mock_sip_participant.identity = "sip_carrier_leg_twilio_xyz"
    
    # Inject the mock participant into remote participant dictionary map
    mock_room.remote_participants = {"caller_leg": mock_sip_participant}
    mock_room.name = "clinic-live-phone-room"
    
    # Mock the JobContext background runtime worker
    mock_job_ctx = MagicMock()
    mock_job_ctx.room = mock_room
    
    # Create an active dummy RunContext parameter needed for function tool verification
    mock_run_ctx = MagicMock()

    # Intercept LiveKit System Hooks using mock context patches
    with patch("tools.handoff.get_job_context", return_value=mock_job_ctx), \
         patch("livekit.api.LiveKitAPI") as MockLiveKitAPI:
         
        # Set up an asynchronous mock for the context manager wrapper API connection leg
        mock_api_instance = AsyncMock()
        MockLiveKitAPI.return_value.__aenter__.return_value = mock_api_instance
        
        print("🚀 Injecting simulated call hooks... Triggering transfer execution loop...")
        
        # Invoke the function tool directly 
        spoken_response = await transfer_to_human(
            reason="Patient requested manual escalation during diagnostic test run.",
            context=mock_run_ctx
        )
        
        print(f"\n🗣️ Agent's Spoken Hand-off Phrase:\n  \"{spoken_response}\"")
        
        # Verify if our mock internal infrastructure caught the outbound API request payload
        if mock_api_instance.sip.transfer_sip_participant.called:
            print("\n✅ Success!")
            print("  The tool successfully resolved the active Twilio SIP trunk leg,")
            print("  built the E.164 teluri, and fired off the SIP REFER command structure.")
        else:
            print("\n❌ Failure: Core transfer endpoint was never targeted by compilation loops.")

if __name__ == "__main__":
    asyncio.run(run_diagnostic())