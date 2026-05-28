"""
Update the Twilio voice webhook TwiML to point at the LiveKit SIP endpoint.
Tries to update the existing TwiML Bin in-place (preserves the voice_url).
Falls back to creating a new bin. Falls back to manual instructions if the
TwiML Bins API is unavailable (e.g. Twilio trial accounts return 404).

Key: use bare digits in the SIP URI user part — LiveKit does NOT URL-decode
%2B, so sip:%2B18167207794@... fails trunk matching. Use sip:18167207794@...

Run: python scripts/update_twiml_bin.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from twilio.rest import Client

# LiveKit SIP does not URL-decode the user part — use bare digits (no + or %2B).
_bare = (config.TWILIO_PHONE_NUMBER or "").lstrip("+")
TWIML_BODY = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Dial>
    <Sip>sip:{_bare}@{config.LIVEKIT_SIP_URI};transport=tcp</Sip>
  </Dial>
</Response>"""


def _extract_bin_sid(voice_url: str) -> str | None:
    m = re.search(r"handler\.twilio\.com/twiml/(\w+)", voice_url or "")
    return m.group(1) if m else None


def main() -> None:
    if not config.TWILIO_PHONE_NUMBER:
        print("TWILIO_PHONE_NUMBER is not set in .env", file=sys.stderr)
        sys.exit(1)

    client = Client(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)

    numbers = client.incoming_phone_numbers.list(phone_number=config.TWILIO_PHONE_NUMBER)
    if not numbers:
        print(f"No Twilio number {config.TWILIO_PHONE_NUMBER}", file=sys.stderr)
        sys.exit(1)

    current_voice_url = numbers[0].voice_url or ""
    existing_sid = _extract_bin_sid(current_voice_url)

    print(f"Phone number : {config.TWILIO_PHONE_NUMBER}")
    print(f"Voice URL    : {current_voice_url}")
    print(f"Target TwiML :\n{TWIML_BODY}\n")

    # Try to update existing bin in-place (preserves the voice_url).
    if existing_sid:
        resp = client.request(
            "POST",
            f"https://content.twilio.com/v1/TwiMLBins/{existing_sid}",
            data={"Body": TWIML_BODY},
        )
        if getattr(resp, "ok", False):
            print(f"Updated existing TwiML Bin : {existing_sid}")
            print(f"Voice webhook unchanged    : {current_voice_url}")
            return
        status = getattr(resp, "status_code", "?")
        print(f"In-place update failed (HTTP {status}) — trying to create new bin...")

    # Fall back to creating a new bin.
    resp = client.request(
        "POST",
        "https://content.twilio.com/v1/TwiMLBins",
        data={"FriendlyName": "The Clinic — LiveKit SIP", "Body": TWIML_BODY},
    )
    if getattr(resp, "ok", False):
        payload = json.loads(resp.content)
        new_url = payload["url"]
        numbers[0].update(voice_url=new_url, voice_method="GET")
        print(f"Created new TwiML Bin : {payload['sid']}")
        print(f"Voice webhook updated : {new_url}")
        return

    # Both API paths failed — print manual instructions.
    status = getattr(resp, "status_code", "?")
    print(
        f"\nAPI unavailable (HTTP {status}) — Twilio trial accounts cannot use the TwiML Bins API.\n"
        "\nUpdate the TwiML Bin manually:\n"
        "  1. Open https://console.twilio.com/us1/develop/twiml-bins\n"
        f"  2. Find the bin linked to {config.TWILIO_PHONE_NUMBER} "
        f"(SID ends with ...{(existing_sid or '')[-8:]})\n"
        "  3. Replace its content with the TwiML printed above\n"
        "  4. Click Save\n"
        "\nCritical: use bare digits in the SIP URI (no + or %2B) — LiveKit won't match %2B.",
        file=sys.stderr,
    )
    sys.exit(1)


if __name__ == "__main__":
    main()
