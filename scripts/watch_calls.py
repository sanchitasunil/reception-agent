"""
Real-time call monitor: shows when SIP calls arrive and leave LiveKit.
Run in a separate terminal while making test calls.
Run: python scripts/watch_calls.py
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from livekit import api
from livekit.protocol.room import ListRoomsRequest, ListParticipantsRequest


async def main() -> None:
    lk = api.LiveKitAPI(
        url=config.LIVEKIT_URL,
        api_key=config.LIVEKIT_API_KEY,
        api_secret=config.LIVEKIT_API_SECRET,
    )

    print(f"Watching {config.LIVEKIT_URL} for clinic-* rooms (Ctrl+C to stop)\n")

    seen: dict[str, float] = {}   # room_name → time first seen
    last_status_print = 0.0

    try:
        while True:
            try:
                rooms_resp = await lk.room.list_rooms(ListRoomsRequest())
                clinic = {r.name: r for r in rooms_resp.rooms if r.name.startswith("clinic-")}

                # Rooms that just appeared
                for name, r in clinic.items():
                    if name not in seen:
                        seen[name] = time.monotonic()
                        ts = time.strftime("%H:%M:%S")
                        try:
                            parts = await lk.room.list_participants(
                                ListParticipantsRequest(room=name)
                            )
                            kinds = [p.kind for p in parts.participants]
                        except Exception:
                            kinds = []
                        print(f"[{ts}] CALL STARTED  {name}")
                        print(f"         participants={r.num_participants}  kinds={kinds}")

                # Rooms that just disappeared
                for name in list(seen):
                    if name not in clinic:
                        duration = time.monotonic() - seen.pop(name)
                        ts = time.strftime("%H:%M:%S")
                        print(f"[{ts}] CALL ENDED    {name}  duration={duration:.0f}s")

                # Periodic heartbeat for ongoing calls
                now = time.monotonic()
                if clinic and now - last_status_print > 10:
                    last_status_print = now
                    for name, r in clinic.items():
                        age = now - seen.get(name, now)
                        ts = time.strftime("%H:%M:%S")
                        print(f"[{ts}] IN PROGRESS   {name}  participants={r.num_participants}  age={age:.0f}s")

                if not clinic and not seen:
                    # Print a dot every 10s so the user knows it's still running
                    if int(time.monotonic()) % 10 == 0:
                        print(f"[{time.strftime('%H:%M:%S')}] waiting for calls...", end="\r")

            except Exception as exc:
                print(f"[{time.strftime('%H:%M:%S')}] poll error: {exc}")

            await asyncio.sleep(2)

    except KeyboardInterrupt:
        pass
    finally:
        await lk.aclose()
        print("\nStopped.")


if __name__ == "__main__":
    asyncio.run(main())
