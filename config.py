from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ValueError(
            f"Required environment variable '{name}' is not set. "
            "Copy .env.example to .env and fill in your credentials."
        )
    return value


def _normalize_sip_uri(uri: str) -> str:
    """Strip sip: prefix; TwiML adds it when connecting to LiveKit."""
    value = uri.strip()
    if value.lower().startswith("sip:"):
        value = value[4:]
    return value


def _require_any(*names: str) -> str:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    raise ValueError(
        f"Required environment variable missing (expected one of: {', '.join(names)}). "
        "Copy .env.example to .env and fill in your credentials."
    )


# LiveKit
LIVEKIT_URL: str = _require("LIVEKIT_URL")
LIVEKIT_API_KEY: str = _require("LIVEKIT_API_KEY")
LIVEKIT_API_SECRET: str = _require("LIVEKIT_API_SECRET")
LIVEKIT_SIP_URI: str = _normalize_sip_uri(_require("LIVEKIT_SIP_URI"))

# Deepgram STT
DEEPGRAM_API_KEY: str = _require("DEEPGRAM_API_KEY")

# Google Gemini LLM
GOOGLE_API_KEY: str = _require("GOOGLE_API_KEY")

# Murf TTS
MURF_API_KEY: str = _require("MURF_API_KEY")

# Twilio — SIP telephony and future SMS/WhatsApp (not used in agent runtime directly).
TWILIO_ACCOUNT_SID: str = _require_any("TWILIO_ACCOUNT_SID", "TWILLIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN: str = _require_any("TWILIO_AUTH_TOKEN", "TWILLIO_AUTH_TOKEN")
TWILIO_PHONE_NUMBER: str | None = os.getenv("TWILIO_PHONE_NUMBER") or os.getenv(
    "TWILLIO_PHONE_NUMBER"
)
TWILIO_WHATSAPP_FROM: str = _require("TWILIO_WHATSAPP_FROM")

# Supabase — caller memory
SUPABASE_URL: str = _require("SUPABASE_URL")
SUPABASE_KEY: str = _require("SUPABASE_KEY")

# Google Calendar mirror (optional — agent works without this)
GOOGLE_CALENDAR_CREDENTIALS_JSON: str | None = os.getenv("GOOGLE_CALENDAR_CREDENTIALS_JSON")
GOOGLE_CALENDAR_ID_MEERA: str | None = os.getenv("GOOGLE_CALENDAR_ID_MEERA")
GOOGLE_CALENDAR_ID_ARUN: str | None = os.getenv("GOOGLE_CALENDAR_ID_ARUN")


def calendar_enabled() -> bool:
    return all(
        [
            GOOGLE_CALENDAR_CREDENTIALS_JSON,
            GOOGLE_CALENDAR_ID_MEERA,
            GOOGLE_CALENDAR_ID_ARUN,
        ]
    )
