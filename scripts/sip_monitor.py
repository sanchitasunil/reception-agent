"""
SIP auto-dispatch monitor.

LiveKit's room_config.agents auto-dispatch does not fire reliably for
SIP-created rooms. This script polls every second, detects new clinic- rooms
that contain a SIP participant but no agent, and dispatches clinic-agent via
CreateAgentDispatchRequest (the same mechanism that test_worker_dispatch.py
uses, which is known to work).

Run this alongside agent.py dev in a second terminal:
    python scripts/sip_monitor.py
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from livekit import api
from livekit.protocol.agent_dispatch import CreateAgentDispatchRequest
from livekit.protocol.room import ListParticipantsRequest, ListRoomsRequest

# Participant kind values from livekit.protocol.models ParticipantInfo.Kind
_KIND_SIP = 3
_KIND_AGENT = 4


async def main() -> None:
    lk = api.LiveKitAPI(
        url=config.LIVEKIT_URL,
        api_key=config.LIVEKIT_API_KEY,
        api_secret=config.LIVEKIT_API_SECRET,
    )

    print(f"SIP monitor running — watching {config.LIVEKIT_URL}")
    print("Dispatches clinic-agent to any new SIP clinic- room (Ctrl+C to stop)\n")

    dispatched: set[str] = set()

    try:
        while True:
            try:
                resp = await lk.room.list_rooms(ListRoomsRequest())
                sip_rooms = [
                    r for r in resp.rooms
                    if r.name.startswith("clinic-")
                    and not r.name.startswith("clinic-test-")
                ]

                current_names = {r.name for r in resp.rooms}
                dispatched &= current_names  # drop rooms that have ended

                for room in sip_rooms:
                    if room.name in dispatched:
                        continue

                    try:
                        parts = await lk.room.list_participants(
                            ListParticipantsRequest(room=room.name)
                        )
                        has_sip = any(p.kind == _KIND_SIP for p in parts.participants)
                        has_agent = any(p.kind == _KIND_AGENT for p in parts.participants)
                    except Exception:
                        continue

                    if has_agent:
                        dispatched.add(room.name)
                        continue

                    if has_sip:
                        ts = time.strftime("%H:%M:%S")
                        print(f"[{ts}] SIP call in {room.name} — dispatching clinic-agent")
                        try:
                            d = await lk.agent_dispatch.create_dispatch(
                                CreateAgentDispatchRequest(
                                    agent_name="clinic-agent",
                                    room=room.name,
                                )
                            )
                            dispatched.add(room.name)
                            print(f"[{ts}] Dispatched {d.id}")
                        except Exception as exc:
                            print(f"[{ts}] Dispatch failed for {room.name}: {exc}")

            except Exception as exc:
                print(f"[{time.strftime('%H:%M:%S')}] Poll error: {exc}")

            await asyncio.sleep(1)

    except KeyboardInterrupt:
        pass
    finally:
        await lk.aclose()
        print("\nStopped.")


if __name__ == "__main__":
    asyncio.run(main())
