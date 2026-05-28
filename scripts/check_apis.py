"""
Test connectivity to every API the agent depends on.
Run before starting the agent: python scripts/check_apis.py
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402 — validates env vars at import time
from livekit.agents.utils import http_context as _http_ctx


async def check_deepgram() -> bool:
    print("=== Deepgram STT ===")
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://api.deepgram.com/v1/projects",
                headers={"Authorization": f"Token {config.DEEPGRAM_API_KEY}"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    projects = data.get("projects", [])
                    name = projects[0]["name"] if projects else "unknown"
                    print(f"  OK  authenticated, project={name!r}")
                    return True
                else:
                    body = await resp.text()
                    print(f"  FAIL  HTTP {resp.status}: {body[:200]}")
                    print("  Fix: check DEEPGRAM_API_KEY in .env")
                    return False
    except Exception as exc:
        print(f"  FAIL  {exc}")
        return False


async def check_openai_stt() -> bool:
    print("=== OpenAI STT (gpt-4o-transcribe) ===")
    try:
        import aiohttp, io
        # Send a minimal silent WAV (44 bytes) — just enough to validate auth and model access
        wav_header = bytes([
            0x52,0x49,0x46,0x46,0x24,0x00,0x00,0x00,0x57,0x41,0x56,0x45,
            0x66,0x6D,0x74,0x20,0x10,0x00,0x00,0x00,0x01,0x00,0x01,0x00,
            0x22,0x56,0x00,0x00,0x44,0xAC,0x00,0x00,0x02,0x00,0x10,0x00,
            0x64,0x61,0x74,0x61,0x00,0x00,0x00,0x00,
        ])
        form = aiohttp.FormData()
        form.add_field("model", "gpt-4o-transcribe")
        form.add_field("language", "en")
        form.add_field("file", io.BytesIO(wav_header), filename="test.wav", content_type="audio/wav")
        t0 = time.monotonic()
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {config.OPENAI_API_KEY}"},
                data=form,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                elapsed = time.monotonic() - t0
                body = await resp.text()
                if resp.status == 200:
                    print(f"  OK  gpt-4o-transcribe reachable ({elapsed * 1000:.0f}ms)")
                    return True
                elif resp.status in (400, 422):
                    # 400 from a silent/empty file is expected — means auth + model are fine
                    print(f"  OK  authenticated, model accessible ({elapsed * 1000:.0f}ms)")
                    return True
                elif resp.status in (401, 403):
                    print(f"  FAIL  HTTP {resp.status}: {body[:200]}")
                    print("  Fix: check OPENAI_API_KEY in .env")
                    return False
                else:
                    print(f"  FAIL  HTTP {resp.status}: {body[:200]}")
                    return False
    except Exception as exc:
        print(f"  FAIL  {exc}")
        return False


async def check_stt() -> bool:
    if config.STT_PROVIDER == "openai":
        return await check_openai_stt()
    return await check_deepgram()


async def check_murf() -> bool:
    print("=== Murf TTS ===")
    try:
        from livekit.plugins import murf
        tts = murf.TTS(voice="en-IN-anisha", locale="en-IN")
        frames: list = []
        t0 = time.monotonic()
        async with _http_ctx.open():
            async for audio in tts.synthesize("Test."):
                frames.append(audio.frame)
        elapsed = time.monotonic() - t0
        total_s = sum(f.duration for f in frames)
        await tts.aclose()
        print(f"  OK  {len(frames)} frames, {total_s:.1f}s audio, latency={elapsed * 1000:.0f}ms")
        return True
    except Exception as exc:
        msg = str(exc)
        print(f"  FAIL  {msg[:200]}")
        if "401" in msg or "403" in msg or "auth" in msg.lower():
            print("  Fix: check MURF_API_KEY in .env")
        return False


async def check_gemini() -> bool:
    print("=== Gemini LLM ===")
    try:
        from google import genai
        client = genai.Client(api_key=config.GOOGLE_API_KEY)
        t0 = time.monotonic()
        resp = await asyncio.to_thread(
            client.models.generate_content,
            model="gemini-2.0-flash",
            contents="Reply with exactly three words: API is OK",
        )
        elapsed = time.monotonic() - t0
        text = (resp.text or "").strip()
        print(f"  OK  response={text!r} ({elapsed * 1000:.0f}ms)")
        return True
    except Exception as exc:
        msg = str(exc)
        if "429" in msg or "RESOURCE_EXHAUSTED" in msg or "quota" in msg.lower():
            print(f"  RATE LIMITED  {msg[:200]}")
            print("  Fix: enable billing at https://aistudio.google.com")
            print("  Workaround: switch to gemini-2.0-flash (15 RPM free tier)")
        elif "401" in msg or "403" in msg or "API_KEY" in msg:
            print(f"  AUTH FAIL  {msg[:200]}")
            print("  Fix: check GOOGLE_API_KEY in .env")
        else:
            print(f"  FAIL  {msg[:200]}")
        return False


async def _opencode_call(session: "aiohttp.ClientSession", model: str) -> tuple[bool, str]:
    import aiohttp
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "Reply with exactly three words: API is OK"}],
        "max_tokens": 16,
        "stream": False,
    }
    t0 = time.monotonic()
    async with session.post(
        "https://opencode.ai/zen/go/v1/chat/completions",
        json=payload,
        headers={"Authorization": f"Bearer {config.OPENCODE_API_KEY}"},
        timeout=aiohttp.ClientTimeout(total=15),
    ) as resp:
        body = await resp.text()
        elapsed = time.monotonic() - t0
        if resp.status == 200:
            import json as _json
            data = _json.loads(body)
            text = data["choices"][0]["message"]["content"].strip()
            return True, f"response={text!r} ({elapsed * 1000:.0f}ms)"
        return False, f"HTTP {resp.status}: {body[:200]}"


async def check_opencode() -> bool:
    print("=== OpenCode LLM ===")
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            ok, msg = await _opencode_call(session, "deepseek-v4-flash")
            if ok:
                print(f"  OK  deepseek-v4-flash — {msg}")
                return True
            print(f"  FAIL  deepseek-v4-flash — {msg}")
            # Try a fallback model to tell apart "model down" from "bad key"
            ok2, msg2 = await _opencode_call(session, "kimi-k2.5")
            if ok2:
                print(f"  WARN  kimi-k2.5 works — deepseek-v4-flash may be temporarily down")
                print(f"        Switch agent to kimi-k2.5 or retry later")
                return False
            if "401" in msg or "403" in msg or "401" in msg2 or "403" in msg2:
                print("  Fix: check OPENCODE_API_KEY in .env")
            else:
                print("  OpenCode server error — try again later or switch model")
            return False
    except Exception as exc:
        print(f"  FAIL  {exc}")
        return False


async def check_ollama() -> bool:
    print("=== Ollama LLM (llama3.2:3b) ===")
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "http://localhost:11434/api/tags",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status != 200:
                    print("  FAIL  Ollama not running — start it or run: ollama serve")
                    return False
                data = await resp.json()
                models = [m["name"] for m in data.get("models", [])]
                if not any("llama3.2" in m for m in models):
                    print(f"  FAIL  llama3.2:3b not pulled. Run: ollama pull llama3.2:3b")
                    print(f"        Available models: {models or 'none'}")
                    return False
                print(f"  OK  Ollama running, llama3.2:3b available")
                return True
    except Exception as exc:
        print(f"  FAIL  {exc}")
        print("  Fix: install Ollama from ollama.com and run: ollama pull llama3.2:3b")
        return False


async def check_openai_llm() -> bool:
    print("=== OpenAI LLM (gpt-4o-mini) ===")
    try:
        import aiohttp
        payload = {
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "Reply with exactly three words: API is OK"}],
            "max_tokens": 16,
        }
        t0 = time.monotonic()
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.openai.com/v1/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {config.OPENAI_API_KEY}"},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                elapsed = time.monotonic() - t0
                body = await resp.text()
                if resp.status == 200:
                    import json as _json
                    data = _json.loads(body)
                    text = data["choices"][0]["message"]["content"].strip()
                    print(f"  OK  response={text!r} ({elapsed * 1000:.0f}ms)")
                    return True
                elif resp.status in (401, 403):
                    print(f"  FAIL  HTTP {resp.status}: {body[:200]}")
                    print("  Fix: check OPENAI_API_KEY in .env")
                    return False
                else:
                    print(f"  FAIL  HTTP {resp.status}: {body[:200]}")
                    return False
    except Exception as exc:
        print(f"  FAIL  {exc}")
        return False


async def check_openai_realtime() -> bool:
    print("=== OpenAI Realtime (gpt-4o-realtime-preview) ===")
    try:
        import aiohttp
        # Validate the key has realtime access via the models endpoint
        t0 = time.monotonic()
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://api.openai.com/v1/models/gpt-4o-realtime-preview",
                headers={"Authorization": f"Bearer {config.OPENAI_API_KEY}"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                elapsed = time.monotonic() - t0
                body = await resp.text()
                if resp.status == 200:
                    print(f"  OK  gpt-4o-realtime-preview accessible ({elapsed * 1000:.0f}ms)")
                    return True
                elif resp.status in (401, 403):
                    print(f"  FAIL  HTTP {resp.status}: {body[:200]}")
                    print("  Fix: check OPENAI_API_KEY in .env")
                    return False
                elif resp.status == 404:
                    print(f"  FAIL  gpt-4o-realtime-preview not available on this account")
                    print("  Fix: ensure your OpenAI account has Realtime API access")
                    return False
                else:
                    print(f"  FAIL  HTTP {resp.status}: {body[:200]}")
                    return False
    except Exception as exc:
        print(f"  FAIL  {exc}")
        return False


async def check_llm() -> bool:
    if config.LLM_PROVIDER == "gemini":
        return await check_gemini()
    elif config.LLM_PROVIDER == "ollama":
        return await check_ollama()
    elif config.LLM_PROVIDER == "openai":
        return await check_openai_llm()
    elif config.LLM_PROVIDER == "realtime":
        return await check_openai_realtime()
    else:
        return await check_opencode()


async def check_livekit() -> bool:
    print("=== LiveKit ===")
    try:
        from livekit import api
        from livekit.protocol.room import ListRoomsRequest
        from livekit.protocol.sip import ListSIPInboundTrunkRequest, ListSIPDispatchRuleRequest

        lk = api.LiveKitAPI(
            url=config.LIVEKIT_URL,
            api_key=config.LIVEKIT_API_KEY,
            api_secret=config.LIVEKIT_API_SECRET,
        )
        rooms = await lk.room.list_rooms(ListRoomsRequest())
        trunks = await lk.sip.list_inbound_trunk(ListSIPInboundTrunkRequest())
        rules = await lk.sip.list_dispatch_rule(ListSIPDispatchRuleRequest())
        await lk.aclose()

        trunk_numbers = []
        for t in trunks.items:
            trunk_numbers.extend(t.numbers)

        agent_rules = [
            r for r in rules.items
            if r.room_config and any(
                a.agent_name == "clinic-agent"
                for a in (r.room_config.agents or [])
            )
        ]

        print(f"  OK  url={config.LIVEKIT_URL!r}")
        print(f"      active_rooms={len(rooms.rooms)}")
        print(f"      inbound_trunks={len(trunks.items)}  numbers={trunk_numbers}")
        print(f"      dispatch_rules_for_clinic-agent={len(agent_rules)}")

        if not trunks.items:
            print("  WARN  No inbound SIP trunk — run: python scripts/setup_twilio_sip.py")
        if not agent_rules:
            print("  WARN  No dispatch rule for clinic-agent — run: python scripts/setup_twilio_sip.py")

        phone_bare = (config.TWILIO_PHONE_NUMBER or "").lstrip("+")
        phone_e164 = config.TWILIO_PHONE_NUMBER or ""
        for t in trunks.items:
            nums = list(t.numbers)
            if phone_bare not in nums and phone_e164 not in nums:
                print(f"  WARN  Trunk {t.sip_trunk_id} doesn't include {phone_e164!r} — run: python scripts/fix_sip_trunk.py")

        return True
    except Exception as exc:
        print(f"  FAIL  {exc}")
        print("  Fix: check LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET in .env")
        return False


async def check_supabase() -> bool:
    print("=== Supabase ===")
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{config.SUPABASE_URL}/rest/v1/",
                headers={
                    "apikey": config.SUPABASE_KEY,
                    "Authorization": f"Bearer {config.SUPABASE_KEY}",
                },
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                # 200 = tables exist, 404 = no tables yet (both mean auth OK)
                if resp.status in (200, 404):
                    print(f"  OK  reachable (HTTP {resp.status}), url={config.SUPABASE_URL!r}")
                    return True
                else:
                    body = await resp.text()
                    print(f"  FAIL  HTTP {resp.status}: {body[:200]}")
                    print("  Fix: check SUPABASE_URL and SUPABASE_KEY in .env")
                    return False
    except Exception as exc:
        print(f"  FAIL  {exc}")
        return False


async def main() -> None:
    checks = [
        ("STT",      check_stt()),
        ("Murf",     check_murf()),
        ("LLM",      check_llm()),
        ("LiveKit",  check_livekit()),
        ("Supabase", check_supabase()),
    ]

    results: dict[str, bool] = {}
    for name, coro in checks:
        results[name] = await coro
        print()

    failed = [k for k, v in results.items() if not v]
    if failed:
        print(f"FAIL: {', '.join(failed)}")
        sys.exit(1)
    else:
        print("All APIs OK. Run: python agent.py dev")


if __name__ == "__main__":
    asyncio.run(main())
