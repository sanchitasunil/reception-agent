"""
Check Twilio + LiveKit SIP wiring. Run while agent.py dev is NOT required.
Run: python scripts/diagnose_telephony.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from livekit import api
from livekit.protocol.sip import ListSIPDispatchRuleRequest, ListSIPInboundTrunkRequest
from twilio.rest import Client


async def main() -> None:
    ok = True

    print("=== Twilio ===")
    if not config.TWILIO_PHONE_NUMBER:
        print("FAIL: TWILIO_PHONE_NUMBER not set")
        ok = False
    else:
        client = Client(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)
        numbers = client.incoming_phone_numbers.list(
            phone_number=config.TWILIO_PHONE_NUMBER
        )
        if not numbers:
            print(f"FAIL: no Twilio number {config.TWILIO_PHONE_NUMBER}")
            ok = False
        else:
            n = numbers[0]
            print(f"Phone     : {n.phone_number}")
            print(f"Voice URL : {n.voice_url}")
            if "handler.twilio.com/twiml" not in (n.voice_url or ""):
                print("WARN: voice URL should be a TwiML Bin handler URL")
                ok = False

    print("\n=== LiveKit SIP ===")
    print(f"SIP URI   : {config.LIVEKIT_SIP_URI}")

    lk = api.LiveKitAPI(
        url=config.LIVEKIT_URL,
        api_key=config.LIVEKIT_API_KEY,
        api_secret=config.LIVEKIT_API_SECRET,
    )
    trunks = await lk.sip.list_inbound_trunk(ListSIPInboundTrunkRequest())
    rules = await lk.sip.list_dispatch_rule(ListSIPDispatchRuleRequest())

    if not trunks.items:
        print("FAIL: no inbound SIP trunk")
        ok = False
    for t in trunks.items:
        print(f"Trunk     : {t.sip_trunk_id} ({t.name}) numbers={list(t.numbers)}")

    if not rules.items:
        print("FAIL: no dispatch rules")
        ok = False
    for r in rules.items:
        agents = (
            [a.agent_name for a in r.room_config.agents]
            if r.room_config and r.room_config.agents
            else []
        )
        print(
            f"Rule      : {r.sip_dispatch_rule_id} ({r.name}) "
            f"trunks={list(r.trunk_ids)} agents={agents}"
        )
        if "clinic-agent" not in agents:
            print("  FAIL: dispatch rule does not dispatch clinic-agent")
            ok = False
        if not r.trunk_ids:
            print("  WARN: rule has empty trunk_ids (matches all trunks)")

    await lk.aclose()

    print()
    if ok:
        print("OK: telephony wiring looks correct. Run: python agent.py dev")
    else:
        print("FIX: python scripts/setup_twilio_sip.py")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
