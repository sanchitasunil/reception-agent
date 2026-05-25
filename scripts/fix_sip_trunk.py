"""
Update the LiveKit inbound SIP trunk to accept both +E.164 and bare E.164
number formats, since Twilio may strip the leading '+' from the SIP URI.

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
    lk = api.LiveKitAPI(
        url=config.LIVEKIT_URL,
        api_key=config.LIVEKIT_API_KEY,
        api_secret=config.LIVEKIT_API_SECRET,
    )

    trunks = await lk.sip.list_inbound_trunk(ListSIPInboundTrunkRequest())
    if not trunks.items:
        print("No inbound trunks found", file=sys.stderr)
        await lk.aclose()
        sys.exit(1)

    number = config.TWILIO_PHONE_NUMBER   # e.g. +18167207794
    bare = number.lstrip("+")             # e.g.  18167207794
    both = [number, bare]

    for trunk in trunks.items:
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
    print("\nDone. Try a call now.")


if __name__ == "__main__":
    asyncio.run(main())
