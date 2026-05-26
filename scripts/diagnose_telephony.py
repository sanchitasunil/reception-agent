"""
Check Twilio + LiveKit SIP wiring. Run while agent.py dev is NOT required.
Run: python scripts/diagnose_telephony.py
"""

from __future__ import annotations

import asyncio
import sys
import urllib.request
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
            else:
                # Fetch the actual TwiML content via the content API.
                import re as _re
                bin_sid_m = _re.search(r"/twiml/(\w+)", n.voice_url or "")
                bin_sid = bin_sid_m.group(1) if bin_sid_m else None
                twiml_content: str | None = None
                if bin_sid:
                    try:
                        resp = client.request(
                            "GET",
                            f"https://content.twilio.com/v1/TwiMLBins/{bin_sid}",
                        )
                        if getattr(resp, "ok", False):
                            import json as _json
                            payload = _json.loads(resp.content)
                            twiml_content = payload.get("body") or payload.get("Body")
                        else:
                            print(f"  INFO: TwiML Bin API returned HTTP {getattr(resp, 'status_code', '?')} (trial accounts may see 404)")
                    except Exception as exc:
                        print(f"  WARN: could not fetch TwiML Bin via API: {exc}")

                if twiml_content:
                    print(f"TwiML     :\n{twiml_content}")
                    sip_uri = config.LIVEKIT_SIP_URI or ""
                    phone_bare = (config.TWILIO_PHONE_NUMBER or "").lstrip("+")
                    phone_e164 = (config.TWILIO_PHONE_NUMBER or "").replace("+", "%2B")
                    has_dial_sip = "<Dial>" in twiml_content and "<Sip>" in twiml_content
                    has_host = sip_uri and sip_uri in twiml_content
                    has_number = phone_bare in twiml_content or phone_e164 in twiml_content
                    if not has_dial_sip:
                        print("  FAIL: TwiML missing <Dial><Sip> — calls won't reach LiveKit")
                        ok = False
                    if not has_host:
                        print(f"  FAIL: TwiML does not contain SIP host {sip_uri!r}")
                        ok = False
                    if not has_number:
                        print(f"  WARN: TwiML does not contain phone number — trunk matching may fail")

    # Show recent inbound call legs + any SIP errors to reveal Twilio→LiveKit failures.
    if config.TWILIO_PHONE_NUMBER:
        try:
            recent_calls = client.calls.list(to=config.TWILIO_PHONE_NUMBER, limit=5)
            print(f"\nRecent calls to {config.TWILIO_PHONE_NUMBER} (last 5):")
            if not recent_calls:
                print("  (none)")
            for c in recent_calls:
                caller = getattr(c, "from_", None) or getattr(c, "from_formatted", None) or "?"
                print(f"  {c.start_time}  status={c.status}  duration={c.duration}s  "
                      f"from={caller}  sid={c.sid}")

            # For the most recent call, inspect child (SIP) leg for dial outcome.
            if recent_calls:
                latest = recent_calls[0]
                try:
                    children = client.calls.list(parent_call_sid=latest.sid, limit=5)
                    if children:
                        print(f"\nSIP legs for latest call ({latest.sid}):")
                        for ch in children:
                            print(f"  {ch.start_time}  status={ch.status}  duration={ch.duration}s  "
                                  f"to={ch.to}  answered_by={getattr(ch, 'answered_by', '-')}")
                    else:
                        print(f"\nNo child SIP legs found for latest call — "
                              "TwiML <Dial> may not have fired or SIP was rejected before answering")
                except Exception as exc2:
                    print(f"  WARN: could not fetch child legs: {exc2}")
        except Exception as exc:
            print(f"  WARN: could not list recent calls: {exc}")

        try:
            errors = client.monitor.events.list(
                resource_sid=None, source_ip_address=None, limit=10
            )
            sip_errors = [e for e in errors if "sip" in (e.event_type or "").lower()
                          or "11" in str(getattr(e, "error_code", ""))]
            if sip_errors:
                print("\nRecent Twilio SIP/error events:")
                for e in sip_errors[:5]:
                    print(f"  {e.event_date}  {e.event_type}  error={getattr(e, 'error_code', '-')}  "
                          f"desc={getattr(e, 'description', '-')[:80]}")
        except Exception:
            pass  # monitor API not always available on trial

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

    # List any active clinic- rooms to show whether SIP calls are landing.
    try:
        from livekit.protocol.room import ListRoomsRequest
        rooms_resp = await lk.room.list_rooms(ListRoomsRequest())
        clinic_rooms = [r for r in rooms_resp.rooms if r.name.startswith("clinic-")]
        if clinic_rooms:
            print("\nActive clinic rooms (live calls in progress):")
            for r in clinic_rooms:
                print(f"  {r.name}  participants={r.num_participants}  "
                      f"active_recording={r.active_recording}")
        else:
            print("\nActive clinic rooms: (none — no call in progress right now)")
    except Exception as exc:
        print(f"\nWARN: could not list rooms: {exc}")

    await lk.aclose()

    print()
    if ok:
        print("OK: telephony wiring looks correct. Run: python agent.py dev")
    else:
        print("FIX: python scripts/setup_twilio_sip.py")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
