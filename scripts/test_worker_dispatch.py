"""
Dispatch clinic-agent into a test room (proves the local worker receives jobs).
Run agent.py dev in another terminal first, then:
  python scripts/test_worker_dispatch.py
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from livekit import api
from livekit.protocol.agent_dispatch import CreateAgentDispatchRequest
from livekit.protocol.room import CreateRoomRequest


async def main() -> None:
    room_name = f"clinic-test-{int(time.time())}"
    lk = api.LiveKitAPI(
        url=config.LIVEKIT_URL,
        api_key=config.LIVEKIT_API_KEY,
        api_secret=config.LIVEKIT_API_SECRET,
    )
    try:
        await lk.room.create_room(CreateRoomRequest(name=room_name))
        print(f"Created room: {room_name}")

        dispatch = await lk.agent_dispatch.create_dispatch(
            CreateAgentDispatchRequest(agent_name="clinic-agent", room=room_name)
        )
        print(f"Dispatched agent: {dispatch.id} -> {room_name}")
        print(
            "Check the agent.py dev terminal for "
            "'received job request' and 'Job started for room'."
        )
    finally:
        await lk.aclose()


if __name__ == "__main__":
    asyncio.run(main())
