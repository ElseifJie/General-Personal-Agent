"""End-to-end smoke: synthetic speech -> ASR -> GPA agent -> TTS.

Requires the local server on 127.0.0.1:8100 and real ASR/TTS/model credentials.
The prompt audio is synthesized with the configured TTS voice, resampled to
16 kHz, streamed to ``/ws/session``, and the reply audio/metrics are asserted.

    python -m scripts.e2e_voice_gpa
"""

from __future__ import annotations

import array
import asyncio
import base64
import json
import time

import httpx
import websockets

from general_personal_agent.clients.tts import TTSClient
from general_personal_agent.db import Database
from general_personal_agent.settings import get_settings


def resample_24k_to_16k(pcm: bytes) -> bytes:
    source = array.array("h")
    source.frombytes(pcm)
    if not len(source):
        return b""
    count = int(len(source) * 16_000 / 24_000)
    output = array.array("h", (source[min(len(source) - 1, int(index * 1.5))] for index in range(count)))
    return output.tobytes()


async def main() -> None:
    settings = get_settings()
    tts = TTSClient(
        settings.tool_vespeech_app_id,
        settings.tool_vespeech_api_key,
        settings.tool_vespeech_speaker,
        cluster=settings.tts_cluster,
        speed_ratio=settings.tool_vespeech_speed_ratio,
    )
    db = await Database.connect(settings)
    conversation_id = ""
    try:
        prompts = ["你好，请用一句话介绍你自己。", "谢谢，请再说一句。"]
        pcm_chunks: list[bytes] = []
        for prompt in prompts:
            pcm24 = await tts.synthesize_pcm(prompt)
            pcm_chunks.append(resample_24k_to_16k(pcm24))
            print(f"synthesized {len(pcm24)} bytes (24k) -> {len(pcm_chunks[-1])} bytes (16k)")

        pin = settings.demo_login_pin
        async with httpx.AsyncClient(base_url="http://127.0.0.1:8100") as client:
            login = await client.post(
                "/api/auth/login", json={"user_id": "alice", "pin": pin}
            )
            assert login.status_code == 200, login.text
            cookie = client.cookies.get(settings.session_cookie_name)
            created = await client.post(
                "/api/conversations", json={"title": "P3 voice e2e", "channel": "voice"}
            )
            assert created.status_code == 200, created.text
            conversation_id = created.json()["conversationId"]
            cookie_header = f"{settings.session_cookie_name}={cookie}"

        url = f"ws://127.0.0.1:8100/ws/session?conversationId={conversation_id}"
        async with websockets.connect(url, additional_headers={"Cookie": cookie_header}) as ws:
            ready = json.loads(await asyncio.wait_for(ws.recv(), 10))
            assert ready["type"] == "ready", ready
            assert ready["conversationId"] == conversation_id
            frame = 640  # 20 ms of 16 kHz s16le
            for index, pcm16 in enumerate(pcm_chunks, start=1):
                messages: list[dict] = []
                started = time.perf_counter()
                first_audio_ms = 0
                for offset in range(0, len(pcm16), frame):
                    chunk = pcm16[offset : offset + frame]
                    await ws.send(
                        json.dumps(
                            {"type": "audio", "data": base64.b64encode(chunk).decode()}
                        )
                    )
                    await asyncio.sleep(0.01)
                await ws.send(json.dumps({"type": "audio_end"}))
                while True:
                    raw = await asyncio.wait_for(ws.recv(), 60)
                    message = json.loads(raw)
                    messages.append(message)
                    kind = message.get("type")
                    if kind == "audio" and not first_audio_ms:
                        first_audio_ms = int((time.perf_counter() - started) * 1000)
                    if kind in {"turn.metrics", "error"}:
                        break
                kinds = [m.get("type") for m in messages]
                metrics = next(
                    (m for m in messages if m.get("type") == "turn.metrics"), {}
                )
                final_text = next(
                    (m.get("text", "") for m in messages if m.get("type") == "final"), ""
                )
                print(f"turn {index}: final={final_text!r}")
                print(f"turn {index}: {metrics.get('metrics')} wall_first_audio_ms={first_audio_ms}")
                assert final_text.strip(), "ASR returned no text"
                assert "text.delta" in kinds, kinds
                assert "audio" in kinds, kinds
                assert metrics.get("outcome") == "completed", metrics
        print("P3 voice e2e PASS (2 turns)")
    finally:
        if conversation_id:
            await db.pool.execute("DELETE FROM task_run WHERE conversation_id = $1", conversation_id)
            await db.pool.execute(
                "DELETE FROM conversation WHERE conversation_id = $1", conversation_id
            )
            await db.pool.execute(
                "DELETE FROM context_trace WHERE conversation_id = $1", conversation_id
            )
        await tts.close()
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
