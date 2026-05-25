# Call flow:
# Phone call → Twilio number → TwiML Bin → LiveKit SIP URI
# → SIP inbound trunk → dispatch rule → clinic-agent worker
# → AgentSession (Deepgram STT → Gemini LLM → Murf TTS)

from __future__ import annotations

import asyncio
import logging
import time

from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import (
    Agent,
    AgentSession,
    AutoSubscribe,
    JobContext,
    JobProcess,
    WorkerOptions,
    cli,
)
from livekit.plugins import deepgram, google, silero
from livekit.plugins import murf

import config  # validates required env vars at import time
from prompts.system_prompt import SYSTEM_PROMPT
from tools.appointment import book_appointment, check_availability, get_doctor_list

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("clinic-agent")
logger.setLevel(logging.INFO)

OPENING_LINE = (
    "Hello, thank you for calling Arogya Clinic. I'm Priya, your AI "
    "receptionist. How may I help you today?"
)


class ClinicAgent(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions=SYSTEM_PROMPT,
            tools=[book_appointment, check_availability, get_doctor_list],
        )


def prewarm(proc: JobProcess) -> None:
    """Download VAD model weights and warm up TTS client before the first call arrives."""
    proc.userdata["vad"] = silero.VAD.load()
    try:
        # Reuse TTS instance across calls so the HTTP connection stays warm.
        proc.userdata["tts"] = murf.TTS(voice="en-IN-anisha", locale="en-IN")
        logger.info("Murf TTS client created in prewarm")
    except Exception:
        logger.exception("Failed to create Murf TTS in prewarm — will create per-call")


def _is_phone_room(room_name: str) -> bool:
    return room_name.startswith("clinic-") and not room_name.startswith(
        "clinic-test-"
    )


async def _greet_phone_caller(
    ctx: JobContext, session: AgentSession, t0: float
) -> None:
    """Greet the SIP caller once they are in the room.

    TTS synthesis is fired immediately so it overlaps with participant-join
    wait time — avoiding the 5-second silence that causes SIP to drop calls.
    """
    # Start TTS synthesis right away; don't block on participant join first.
    # The audio track is already published to the room from session.start(), so
    # synthesis can begin even before we know who the caller is.
    handle = session.say(OPENING_LINE, allow_interruptions=False)
    logger.info("TTS synthesis started at %.1fs", time.monotonic() - t0)

    # Resolve participant (usually already present for SIP calls).
    participant: rtc.RemoteParticipant | None = None
    for p in ctx.room.remote_participants.values():
        participant = p
        logger.info(
            "Caller already in room: %s (%s)",
            p.identity,
            rtc.ParticipantKind.Name(p.kind),
        )
        break

    if participant is None:
        try:
            participant = await asyncio.wait_for(
                ctx.wait_for_participant(),
                timeout=20.0,
            )
            logger.info(
                "Caller joined: %s (%s) at %.1fs",
                participant.identity,
                rtc.ParticipantKind.Name(participant.kind),
                time.monotonic() - t0,
            )
        except asyncio.TimeoutError:
            logger.error(
                "No caller in %s after 20s — remote=%s",
                ctx.room.name,
                list(ctx.room.remote_participants.keys()),
            )
            return

    # Subscribe the session to this caller's audio for STT input.
    session.room_io.set_participant(participant.identity)

    await asyncio.wait_for(handle.wait_for_playout(), timeout=60.0)
    logger.info("Opening greeting played at %.1fs", time.monotonic() - t0)


async def entrypoint(ctx: JobContext) -> None:
    t0 = time.monotonic()
    is_phone = _is_phone_room(ctx.room.name)

    # Start session first so the agent publishes an audio track — SIP/Twilio
    # drops immediately if nothing is publishing RTP in the room.
    await ctx.connect(
        auto_subscribe=AutoSubscribe.AUDIO_ONLY
        if is_phone
        else AutoSubscribe.SUBSCRIBE_ALL,
    )
    logger.info("Connected to %s (%.1fs)", ctx.room.name, time.monotonic() - t0)

    tts_instance = ctx.proc.userdata.get("tts") or murf.TTS(
        voice="en-IN-anisha", locale="en-IN"
    )
    session = AgentSession(
        stt=deepgram.STT(model="nova-3", language="en-IN"),
        llm=google.LLM(model="gemini-2.5-flash"),
        tts=tts_instance,
        vad=ctx.proc.userdata["vad"],
    )

    await session.start(ClinicAgent(), room=ctx.room)
    logger.info("Session started (%.1fs)", time.monotonic() - t0)

    if is_phone:
        try:
            await _greet_phone_caller(ctx, session, t0)
        except asyncio.TimeoutError:
            logger.error("Phone greeting timed out — call may have dropped")
        except Exception:
            logger.exception("Phone greeting failed")

    while ctx.room.isconnected():
        await asyncio.sleep(0.25)

    logger.info("Room %s disconnected", ctx.room.name)


if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
            agent_name="clinic-agent",
            num_idle_processes=3,
        )
    )
