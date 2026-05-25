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
from livekit.agents import tts as agents_tts
from livekit.agents.tts import AudioEmitter
from livekit.agents.types import APIConnectOptions
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


# ── Pre-synthesized greeting cache ────────────────────────────────────────────

class _CachedChunkedStream(agents_tts.ChunkedStream):
    """Replays pre-synthesized PCM frames without calling the TTS API."""

    def __init__(
        self,
        *,
        tts_instance: agents_tts.TTS,
        input_text: str,
        frames: list[rtc.AudioFrame],
        conn_options: APIConnectOptions,
    ) -> None:
        super().__init__(tts=tts_instance, input_text=input_text, conn_options=conn_options)
        self._frames = frames

    async def _run(self, output_emitter: AudioEmitter) -> None:
        if not self._frames:
            return
        first = self._frames[0]
        output_emitter.initialize(
            request_id="cached-greeting",
            sample_rate=first.sample_rate,
            num_channels=first.num_channels,
            mime_type="audio/pcm",
        )
        for frame in self._frames:
            output_emitter.push(bytes(frame.data))


class CachedGreetingTTS(agents_tts.TTS):
    """Wraps a TTS and plays pre-cached audio for the opening greeting call."""

    def __init__(
        self,
        inner: agents_tts.TTS,
        greeting_text: str,
        greeting_frames: list[rtc.AudioFrame],
    ) -> None:
        super().__init__(
            capabilities=inner.capabilities,
            sample_rate=inner.sample_rate,
            num_channels=inner.num_channels,
        )
        self._inner = inner
        self._greeting_text = greeting_text
        self._greeting_frames = greeting_frames
        self._greeting_used = False

    def synthesize(
        self,
        text: str,
        *,
        conn_options: APIConnectOptions = APIConnectOptions(),
    ) -> agents_tts.ChunkedStream:
        if not self._greeting_used and self._greeting_frames and text == self._greeting_text:
            self._greeting_used = True
            logger.info("Serving pre-cached greeting (no TTS API call)")
            return _CachedChunkedStream(
                tts_instance=self,
                input_text=text,
                frames=self._greeting_frames,
                conn_options=conn_options,
            )
        return self._inner.synthesize(text, conn_options=conn_options)

    async def aclose(self) -> None:
        await self._inner.aclose()


# ── Agent ──────────────────────────────────────────────────────────────────────

class ClinicAgent(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions=SYSTEM_PROMPT,
            tools=[book_appointment, check_availability, get_doctor_list],
        )


def prewarm(proc: JobProcess) -> None:
    """Load VAD weights and pre-synthesize the opening greeting before first call."""
    proc.userdata["vad"] = silero.VAD.load()

    tts_instance = murf.TTS(voice="en-IN-anisha", locale="en-IN")

    async def _synthesise_greeting() -> list[rtc.AudioFrame]:
        frames: list[rtc.AudioFrame] = []
        async for audio in tts_instance.synthesize(OPENING_LINE):
            frames.append(audio.frame)
        return frames

    try:
        greeting_frames = asyncio.run(_synthesise_greeting())
        total_s = sum(f.duration for f in greeting_frames)
        logger.info(
            "Greeting pre-synthesized: %d frames, %.1fs audio", len(greeting_frames), total_s
        )
        proc.userdata["tts"] = CachedGreetingTTS(tts_instance, OPENING_LINE, greeting_frames)
    except Exception:
        logger.exception("Greeting pre-synthesis failed — will synthesize on first call")
        proc.userdata["tts"] = tts_instance


def _is_phone_room(room_name: str) -> bool:
    return room_name.startswith("clinic-") and not room_name.startswith("clinic-test-")


async def _greet_phone_caller(
    ctx: JobContext, session: AgentSession, t0: float
) -> None:
    """Play opening greeting; TTS is fired immediately to overlap with participant-join wait."""
    handle = session.say(OPENING_LINE, allow_interruptions=False)
    logger.info("Greeting started at %.1fs", time.monotonic() - t0)

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
            participant = await asyncio.wait_for(ctx.wait_for_participant(), timeout=20.0)
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

    session.room_io.set_participant(participant.identity)

    await asyncio.wait_for(handle.wait_for_playout(), timeout=60.0)
    logger.info("Opening greeting played at %.1fs", time.monotonic() - t0)


async def entrypoint(ctx: JobContext) -> None:
    t0 = time.monotonic()
    is_phone = _is_phone_room(ctx.room.name)

    await ctx.connect(
        auto_subscribe=AutoSubscribe.AUDIO_ONLY if is_phone else AutoSubscribe.SUBSCRIBE_ALL,
    )
    logger.info("Connected to %s (%.1fs)", ctx.room.name, time.monotonic() - t0)

    tts_instance = ctx.proc.userdata.get("tts") or murf.TTS(voice="en-IN-anisha", locale="en-IN")
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
