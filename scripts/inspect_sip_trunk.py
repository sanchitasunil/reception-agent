"""
Print the full SIP trunk and dispatch rule config from LiveKit.
Useful to verify exactly what LiveKit has stored vs what we expect.
Run: python scripts/inspect_sip_trunk.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from livekit import api
from livekit.protocol.sip import ListSIPDispatchRuleRequest, ListSIPInboundTrunkRequest


async def main() -> None:
    lk = api.LiveKitAPI(
        url=config.LIVEKIT_URL,
        api_key=config.LIVEKIT_API_KEY,
        api_secret=config.LIVEKIT_API_SECRET,
    )

    print("=== Inbound SIP Trunks ===")
    trunks = await lk.sip.list_inbound_trunk(ListSIPInboundTrunkRequest())
    if not trunks.items:
        print("  (none)")
    for t in trunks.items:
        print(f"  ID       : {t.sip_trunk_id}")
        print(f"  Name     : {t.name}")
        print(f"  Numbers  : {list(t.numbers)}")
        print(f"  Headers  : {dict(t.headers)}")
        print(f"  AllowedAddresses: {list(t.allowed_addresses)}")
        print(f"  AllowedNumbers  : {list(t.allowed_numbers)}")
        print(f"  AuthUsername: {t.auth_username!r}")
        print()

    print("=== SIP Dispatch Rules ===")
    rules = await lk.sip.list_dispatch_rule(ListSIPDispatchRuleRequest())
    if not rules.items:
        print("  (none)")
    for r in rules.items:
        print(f"  ID       : {r.sip_dispatch_rule_id}")
        print(f"  Name     : {r.name}")
        print(f"  Trunks   : {list(r.trunk_ids)}")
        print(f"  Rule type: {r.rule.WhichOneof('rule') if r.rule else 'none'}")
        if r.rule and r.rule.HasField("dispatch_rule_individual"):
            ri = r.rule.dispatch_rule_individual
            print(f"    room_prefix : {ri.room_prefix!r}")
            print(f"    pin         : {ri.pin!r}")
        if r.rule and r.rule.HasField("dispatch_rule_direct"):
            rd = r.rule.dispatch_rule_direct
            print(f"    room_name   : {rd.room_name!r}")
            print(f"    pin         : {rd.pin!r}")
        if r.room_config:
            agents = [a.agent_name for a in (r.room_config.agents or [])]
            print(f"  room_config.agents: {agents}")
            print(f"  room_config.metadata: {r.room_config.metadata!r}")
        else:
            print(f"  room_config: (none — agent dispatch will NOT fire!)")
        print()

    await lk.aclose()


if __name__ == "__main__":
    asyncio.run(main())
