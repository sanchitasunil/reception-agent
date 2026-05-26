"""
Update the LiveKit inbound SIP trunk to accept both +E.164 and bare E.164
number formats (e.g. +18167207794 and 18167207794), because Twilio strips
the leading '+' from the SIP URI user part and LiveKit must match either form.

Run: python scripts/fix_sip_trunk.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from livekit import api
from livekit.protocol.sip import ListSIPInboundTrunkRequest, SIPInboundTrunkInfo


async def main() -> None:
    if not config.TWILIO_PHONE_NUMBER:
        print("Error: TWILIO_PHONE_NUMBER is not set in .env", file=sys.stderr)
        sys.exit(1)

    lk = api.LiveKitAPI(
        url=config.LIVEKIT_URL,
        api_key=config.LIVEKIT_API_KEY,
        api_secret=config.LIVEKIT_API_SECRET,
    )

    trunks = await lk.sip.list_inbound_trunk(ListSIPInboundTrunkRequest())
    if not trunks.items:
        print("No inbound SIP trunks found.", file=sys.stderr)
        print("Run: python scripts/setup_twilio_sip.py", file=sys.stderr)
        await lk.aclose()
        sys.exit(1)

    e164 = config.TWILIO_PHONE_NUMBER          # e.g. +18167207794
    bare = config.TWILIO_PHONE_NUMBER.lstrip("+")  # e.g.  18167207794
    both = [e164, bare]

    for trunk in trunks.items:
        current = list(trunk.numbers)
        if set(current) == set(both):
            print(f"Trunk {trunk.sip_trunk_id} ({trunk.name}) already has both formats: {current}")
            continue

        updated = await lk.sip.update_inbound_trunk(
            trunk.sip_trunk_id,
            SIPInboundTrunkInfo(
                name=trunk.name,
                numbers=both,
            ),
        )
        print(f"Updated trunk {updated.sip_trunk_id} ({updated.name})")
        print(f"  numbers = {list(updated.numbers)}")

    await lk.aclose()
    print("\nDone. Both +E.164 and bare E.164 accepted.")


if __name__ == "__main__":
    asyncio.run(main())
