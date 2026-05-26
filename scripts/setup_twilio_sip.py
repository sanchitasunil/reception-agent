"""
One-time setup script for Twilio + LiveKit SIP integration.
Run: python scripts/setup_twilio_sip.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

# Allow importing config from project root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402  # loads and validates env vars
from livekit import api
from livekit.protocol.agent_dispatch import RoomAgentDispatch
from livekit.protocol.room import RoomConfiguration
from livekit.protocol.sip import (
    CreateSIPDispatchRuleRequest,
    CreateSIPInboundTrunkRequest,
    DeleteSIPDispatchRuleRequest,
    ListSIPDispatchRuleRequest,
    SIPDispatchRule,
    SIPDispatchRuleIndividual,
    SIPDispatchRuleInfo,
    SIPInboundTrunkInfo,
)
from twilio.rest import Client


def _fail(step: str, exc: Exception) -> None:
    print(f"Error in {step}: {exc}", file=sys.stderr)
    sys.exit(1)


def _twiml_body(sip_uri: str, twilio_number: str) -> str:
    # LiveKit SIP does not URL-decode the user part — use bare digits (no + or %2B).
    bare = twilio_number.lstrip("+")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Dial>
    <Sip>sip:{bare}@{sip_uri};transport=tcp</Sip>
  </Dial>
</Response>"""


def step1_create_twiml_bin(
    client: Client, sip_uri: str, twilio_number: str
) -> tuple[str, str]:
    """Create TwiML Bin and return (sid, url)."""
    try:
        response = client.request(
            "POST",
            "https://content.twilio.com/v1/TwiMLBins",
            data={
                "FriendlyName": "Arogya Clinic — LiveKit SIP",
                "Body": _twiml_body(sip_uri, twilio_number),
            },
        )
    except Exception as exc:
        _fail("Step 1 — Create TwiML Bin", exc)

    if not getattr(response, "ok", False):
        status = getattr(response, "status_code", "unknown")
        body = getattr(response, "content", getattr(response, "body", ""))
        _fail(
            "Step 1 — Create TwiML Bin",
            RuntimeError(f"Twilio API returned HTTP {status}: {body}"),
        )

    payload = json.loads(response.content)
    sid = payload.get("sid")
    url = payload.get("url")
    if not sid or not url:
        _fail(
            "Step 1 — Create TwiML Bin",
            RuntimeError(f"Unexpected Twilio response (missing sid/url): {payload}"),
        )

    print(f"TwiML Bin SID : {sid}")
    print(f"TwiML Bin URL : {url}")
    return sid, url


def step2_point_number_at_bin(
    client: Client, phone_number: str, twiml_bin_url: str
) -> None:
    """Assign TwiML Bin URL to the Twilio phone number voice webhook."""
    try:
        numbers = client.incoming_phone_numbers.list(phone_number=phone_number)
        if not numbers:
            _fail(
                "Step 2 — Point phone number at TwiML Bin",
                RuntimeError(
                    f"No incoming phone number found for {phone_number}. "
                    "Check TWILIO_PHONE_NUMBER in .env."
                ),
            )
        numbers[0].update(voice_url=twiml_bin_url, voice_method="GET")
    except SystemExit:
        raise
    except Exception as exc:
        _fail("Step 2 — Point phone number at TwiML Bin", exc)

    print(f"Voice webhook updated for {phone_number}")


async def step3_create_inbound_trunk(
    sip_client: api.SipService, phone_number: str
) -> SIPInboundTrunkInfo:
    try:
        trunk = await sip_client.create_sip_inbound_trunk(
            CreateSIPInboundTrunkRequest(
                trunk=SIPInboundTrunkInfo(
                    name="arogya-clinic-inbound",
                    numbers=[phone_number],
                )
            )
        )
    except Exception as exc:
        _fail("Step 3 — Create LiveKit inbound SIP trunk", exc)

    print(f"SIP trunk ID  : {trunk.sip_trunk_id}")
    return trunk


def _dispatch_rule_is_valid(rule: SIPDispatchRuleInfo, trunk_id: str) -> bool:
    if rule.name != "arogya-clinic-dispatch":
        return False
    if trunk_id not in rule.trunk_ids:
        return False
    if not rule.room_config or not rule.room_config.agents:
        return False
    return any(a.agent_name == "clinic-agent" for a in rule.room_config.agents)


async def _remove_broken_dispatch_rules(sip_client: api.SipService) -> None:
    """Delete dispatch rules missing agent dispatch (common CLI mistake)."""
    existing = await sip_client.list_dispatch_rule(ListSIPDispatchRuleRequest())
    for rule in existing.items:
        agents = (
            [a.agent_name for a in rule.room_config.agents]
            if rule.room_config and rule.room_config.agents
            else []
        )
        if "clinic-agent" not in agents:
            print(f"Removing dispatch rule without agent: {rule.sip_dispatch_rule_id}")
            await sip_client.delete_dispatch_rule(
                DeleteSIPDispatchRuleRequest(sip_dispatch_rule_id=rule.sip_dispatch_rule_id)
            )


async def step4_create_dispatch_rule(
    sip_client: api.SipService, trunk_id: str
) -> SIPDispatchRuleInfo:
    try:
        await _remove_broken_dispatch_rules(sip_client)

        existing = await sip_client.list_dispatch_rule(ListSIPDispatchRuleRequest())
        for rule in existing.items:
            if _dispatch_rule_is_valid(rule, trunk_id):
                print(f"Dispatch rule already OK: {rule.sip_dispatch_rule_id}")
                return rule

        rule = await sip_client.create_dispatch_rule(
            CreateSIPDispatchRuleRequest(
                dispatch_rule=SIPDispatchRuleInfo(
                    name="arogya-clinic-dispatch",
                    trunk_ids=[trunk_id],
                    rule=SIPDispatchRule(
                        dispatch_rule_individual=SIPDispatchRuleIndividual(
                            room_prefix="clinic-",
                        )
                    ),
                    room_config=RoomConfiguration(
                        agents=[
                            RoomAgentDispatch(agent_name="clinic-agent"),
                        ]
                    ),
                )
            )
        )
    except Exception as exc:
        _fail("Step 4 — Create LiveKit dispatch rule", exc)

    print(f"Dispatch rule : {rule.sip_dispatch_rule_id}")
    return rule


async def main() -> None:
    if not config.TWILIO_PHONE_NUMBER:
        print(
            "Error: TWILIO_PHONE_NUMBER is not set in .env",
            file=sys.stderr,
        )
        sys.exit(1)

    twilio_client = Client(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)
    lkapi = api.LiveKitAPI(
        url=config.LIVEKIT_URL,
        api_key=config.LIVEKIT_API_KEY,
        api_secret=config.LIVEKIT_API_SECRET,
    )
    sip_client = lkapi.sip

    print("Step 1 — Create TwiML Bin")
    # config.LIVEKIT_SIP_URI is hostname only (no sip: prefix).
    _, twiml_bin_url = step1_create_twiml_bin(
        twilio_client, config.LIVEKIT_SIP_URI, config.TWILIO_PHONE_NUMBER
    )

    print("\nStep 2 — Point phone number at TwiML Bin")
    step2_point_number_at_bin(
        twilio_client, config.TWILIO_PHONE_NUMBER, twiml_bin_url
    )

    print("\nStep 3 — Create LiveKit inbound SIP trunk")
    trunk = await step3_create_inbound_trunk(sip_client, config.TWILIO_PHONE_NUMBER)

    print("\nStep 4 — Create LiveKit dispatch rule")
    rule = await step4_create_dispatch_rule(sip_client, trunk.sip_trunk_id)

    await lkapi.aclose()

    print("\n✓ Setup complete")
    print(f"  Phone number : {config.TWILIO_PHONE_NUMBER}")
    print(f"  TwiML Bin    : {twiml_bin_url}")
    print(f"  SIP trunk    : {trunk.sip_trunk_id}")
    print(f"  Dispatch rule: {rule.sip_dispatch_rule_id}")
    print(f"\nCall {config.TWILIO_PHONE_NUMBER} to speak to Priya.")


if __name__ == "__main__":
    asyncio.run(main())
