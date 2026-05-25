"""
Update Twilio voice webhook TwiML to point at LiveKit SIP.
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

# Twilio's TwiML validator rejects bare '+' in SIP URI user parts; %2B is equivalent.
_e164 = (config.TWILIO_PHONE_NUMBER or "").replace("+", "%2B")
TWIML_BODY = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Dial>
    <Sip>sip:{_e164}@{config.LIVEKIT_SIP_URI};transport=tcp</Sip>
  </Dial>
</Response>"""


def _extract_bin_sid(voice_url: str) -> str | None:
    """Extract TwiML Bin SID from a handler.twilio.com URL."""
    m = re.search(r"handler\.twilio\.com/twiml/(\S+)", voice_url or "")
    return m.group(1) if m else None


def main() -> None:
    if not config.TWILIO_PHONE_NUMBER:
        print("TWILIO_PHONE_NUMBER is not set", file=sys.stderr)
        sys.exit(1)

    client = Client(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)

    numbers = client.incoming_phone_numbers.list(phone_number=config.TWILIO_PHONE_NUMBER)
    if not numbers:
        print(f"No Twilio number {config.TWILIO_PHONE_NUMBER}", file=sys.stderr)
        sys.exit(1)

    current_voice_url = numbers[0].voice_url or ""
    existing_sid = _extract_bin_sid(current_voice_url)

    # Try to update the existing TwiML Bin in-place so the phone number's
    # voice_url doesn't change.
    if existing_sid:
        resp = client.request(
            "POST",
            f"https://content.twilio.com/v1/TwiMLBins/{existing_sid}",
            data={"Body": TWIML_BODY},
        )
        if resp.ok:
            print(f"Updated existing TwiML Bin : {existing_sid}")
            print(f"SIP target                 : sip:{config.TWILIO_PHONE_NUMBER}@{config.LIVEKIT_SIP_URI};transport=tcp")
            print(f"Voice webhook unchanged    : {current_voice_url}")
            return
        print(f"In-place update failed (HTTP {resp.status_code}), trying to create new bin…")

    # Fall back to creating a new TwiML Bin.
    resp = client.request(
        "POST",
        "https://content.twilio.com/v1/TwiMLBins",
        data={"FriendlyName": "Arogya Clinic — LiveKit SIP", "Body": TWIML_BODY},
    )
    if resp.ok:
        payload = json.loads(resp.content)
        new_url = payload["url"]
        numbers[0].update(voice_url=new_url, voice_method="GET")
        print(f"Created TwiML Bin : {payload['sid']}")
        print(f"Updated voice webhook for {config.TWILIO_PHONE_NUMBER} → {new_url}")
        return

    # Both API paths failed — print manual instructions.
    print(
        f"\nAPI unavailable (HTTP {resp.status_code}). "
        "Update the TwiML Bin manually:\n"
        "\n  1. Open https://console.twilio.com/us1/develop/twiml-bins"
        f"\n  2. Find the bin whose URL ends with  {existing_sid or '(see phone number voice URL)'}"
        "\n  3. Replace its content with:\n",
        file=sys.stderr,
    )
    print(TWIML_BODY, file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
