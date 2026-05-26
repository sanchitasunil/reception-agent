"""
Test SIP handoff config and API without the voice agent.

Run from project root:
  python scripts/test_handoff.py status
  python scripts/test_handoff.py dry-run
  python scripts/test_handoff.py messages
  python scripts/test_handoff.py rooms
  python scripts/test_handoff.py refer --room <real-room> --identity <real-sip-id>

`rooms` lists active clinic-* rooms and SIP participants (run while on a phone call).
`refer` calls the real LiveKit SIP REFER API — do not use placeholder XXXX values.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from livekit import api  # noqa: E402
from tools.handoff import (  # noqa: E402
    CLINIC_FALLBACK,
    _normalize_tel,
    transfer_call,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test_handoff")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Test SIP call handoff (SIP REFER).")
    sub = parser.add_subparsers(dest="action", required=True)

    sub.add_parser("status", help="Show handoff configuration")
    sub.add_parser("dry-run", help="Show REFER target without calling LiveKit")
    sub.add_parser(
        "messages",
        help="Print tool responses for each scenario (no API, no agent)",
    )
    sub.add_parser(
        "rooms",
        help="List active clinic-* rooms and SIP participant identities",
    )

    refer = sub.add_parser(
        "refer",
        help="Call transfer_call (needs active SIP call in that room)",
    )
    refer.add_argument(
        "--room",
        required=True,
        help="LiveKit room name, e.g. clinic-abc123",
    )
    refer.add_argument(
        "--identity",
        required=True,
        help="SIP participant identity from agent logs",
    )
    refer.add_argument(
        "--reason",
        default="manual test via scripts/test_handoff.py",
    )

    return parser.parse_args()


def _cmd_status() -> int:
    print("=== Handoff status ===\n")
    print(f"handoff_enabled(): {config.handoff_enabled()}")
    print(f"CLINIC_PHONE_NUMBER: {config.CLINIC_PHONE_NUMBER or '(not set)'}")
    if config.CLINIC_PHONE_NUMBER:
        print(f"REFER transfer_to: {_normalize_tel(config.CLINIC_PHONE_NUMBER)}")
    print()
    print(f"LIVEKIT_URL: {config.LIVEKIT_URL}")
    print(f"LIVEKIT_API_KEY: {'set' if config.LIVEKIT_API_KEY else 'missing'}")
    print(f"LIVEKIT_API_SECRET: {'set' if config.LIVEKIT_API_SECRET else 'missing'}")
    print()
    if config.handoff_enabled():
        print("During a live call: python scripts/test_handoff.py rooms")
        print("Then: python scripts/test_handoff.py refer --room ... --identity ...")
    else:
        print("Set CLINIC_PHONE_NUMBER in .env to enable transfers.")
        print(f"Agent will read fallback: {CLINIC_FALLBACK}")
    return 0


def _cmd_dry_run() -> int:
    print("=== SIP REFER preview (dry run) ===\n")
    if not config.handoff_enabled():
        print("Handoff disabled — no CLINIC_PHONE_NUMBER in .env")
        return 1

    tel = _normalize_tel(config.CLINIC_PHONE_NUMBER)
    print(f"transfer_to     : {tel}")
    print(f"clinic number   : {config.CLINIC_PHONE_NUMBER}")
    print("play_dialtone   : False")
    print()
    print("LiveKit request (example):")
    print("  room_name              = <from agent logs, e.g. clinic-...>")
    print("  participant_identity   = <SIP participant identity>")
    print(f"  transfer_to            = {tel}")
    print()
    print("No API call made. Use refer during an active call to test for real.")
    return 0


def _cmd_messages() -> int:
    print("=== transfer_to_human responses (offline) ===\n")

    print("1) CLINIC_PHONE_NUMBER not set (current env):")
    if config.handoff_enabled():
        print("   (skipped — handoff is enabled in your .env)")
    else:
        print(
            f'   "Please call us directly on {CLINIC_FALLBACK} '
            f'and our team will help you."'
        )

    print("\n2) No job context (always true outside agent worker):")
    print(
        f'   "I wasn\'t able to connect the transfer. '
        f'Please call us directly on {CLINIC_FALLBACK}."'
    )

    print("\n3) No SIP participant (Playground / no phone call):")
    print(
        f'   "I wasn\'t able to connect the transfer. '
        f'Please call us directly on {CLINIC_FALLBACK}."'
    )

    print("\n4) Successful REFER:")
    print(
        '   "I\'m transferring you to our team right now. '
        'Please hold for just a moment."'
    )

    print("\n5) REFER API failed (Twilio SIP REFER off, bad room, etc.):")
    print(
        f'   "I wasn\'t able to connect the transfer right now. '
        f'Please call us directly on {CLINIC_FALLBACK}."'
    )
    return 0


def _looks_like_placeholder(value: str) -> bool:
    upper = value.upper()
    return "XXXX" in upper or value in ("clinic-XXXX", "sip_XXXX")


async def _cmd_rooms() -> int:
    print("=== Active clinic rooms (LiveKit) ===\n")
    print("Run this while you are on a phone call and agent.py start is running.\n")

    try:
        async with api.LiveKitAPI(
            url=config.LIVEKIT_URL,
            api_key=config.LIVEKIT_API_KEY,
            api_secret=config.LIVEKIT_API_SECRET,
        ) as lk_api:
            listed = await lk_api.room.list_rooms(api.ListRoomsRequest())
            clinic_rooms = [r for r in listed.rooms if r.name.startswith("clinic-")]

            if not clinic_rooms:
                print("No clinic-* rooms right now.")
                print("Start the agent, call your Twilio number, then run this again.")
                return 1

            found_sip = False
            for room in clinic_rooms:
                print(f"Room: {room.name}  (sid={room.sid}, num_participants={room.num_participants})")
                parts = await lk_api.room.list_participants(
                    api.ListParticipantsRequest(room=room.name)
                )
                for p in parts.participants:
                    kind_name = api.ParticipantInfo.Kind.Name(p.kind)
                    marker = "  <-- use for refer --identity" if p.kind == api.ParticipantInfo.Kind.SIP else ""
                    if p.kind == api.ParticipantInfo.Kind.SIP:
                        found_sip = True
                    print(f"  identity={p.identity!r}  kind={kind_name}{marker}")
                print()

            if found_sip:
                print("Copy the room name and SIP identity into:")
                print("  python scripts/test_handoff.py refer --room <room> --identity <identity>")
            else:
                print("No SIP participants in these rooms (Playground users are not SIP).")
            return 0
    except Exception as exc:
        print(f"FAILED: {exc}")
        return 1


async def _cmd_refer(args: argparse.Namespace) -> int:
    print("=== transfer_call (live API) ===\n")
    if not config.handoff_enabled():
        print("Handoff disabled. Set CLINIC_PHONE_NUMBER in .env first.")
        return 1

    if _looks_like_placeholder(args.room) or _looks_like_placeholder(args.identity):
        print("ERROR: clinic-XXXX and sip_XXXX are examples, not real IDs.")
        print("While on a live call, run:  python scripts/test_handoff.py rooms")
        print("Use the room name and SIP identity it prints.")
        return 1

    print(f"room              : {args.room}")
    print(f"participant       : {args.identity}")
    print(f"transfer_to       : {_normalize_tel(config.CLINIC_PHONE_NUMBER)}")
    print(f"reason            : {args.reason}")
    print()

    ok = await transfer_call(
        room_name=args.room,
        sip_participant_identity=args.identity,
        reason=args.reason,
    )

    if ok:
        print("OK — REFER sent. The clinic phone should ring; LiveKit room will close.")
        return 0

    print("FAILED — see error above.")
    print()
    print("participant does not exist (404) usually means:")
    print("  - The call already ended, or room/identity are wrong")
    print("  - You used placeholder values — run: python scripts/test_handoff.py rooms")
    print("    during an active phone call and copy the real values")
    print()
    print("Other causes:")
    print("  - Twilio trunk: enable SIP REFER under Call Transfer")
    return 1


async def main() -> None:
    args = _parse_args()
    if args.action == "status":
        sys.exit(_cmd_status())
    if args.action == "dry-run":
        sys.exit(_cmd_dry_run())
    if args.action == "messages":
        sys.exit(_cmd_messages())
    if args.action == "rooms":
        sys.exit(await _cmd_rooms())
    if args.action == "refer":
        sys.exit(await _cmd_refer(args))


if __name__ == "__main__":
    asyncio.run(main())
