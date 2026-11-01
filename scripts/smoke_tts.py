"""TTS smoke: wav + pcm synthesis."""

from __future__ import annotations

import time
from pathlib import Path

from general_personal_agent.clients.tts import TTSClient
from general_personal_agent.clients.tts_stream import TTSStreamClient
from general_personal_agent.settings import Settings
from scripts._common import check, info, run


async def main(settings: Settings) -> bool:
    client = TTSClient(
        settings.tool_vespeech_app_id,
        settings.tool_vespeech_api_key,
        settings.tool_vespeech_speaker,
        cluster=settings.tts_cluster,
    )
    wav = await client.synthesize_wav("你好，语音合成参数校验通过。")
    pcm = await client.synthesize_pcm("你好，语音合成参数校验通过。")
    Path("/tmp/general_personal_agent_tts.wav").write_bytes(wav)
    ok = check(len(wav) > 10_000, "tts wav", f"{len(wav)} bytes")
    ok &= check(len(pcm) > 10_000, "tts pcm", f"{len(pcm)} bytes / 24kHz")
    info("speaker", settings.tool_vespeech_speaker)

    stream = TTSStreamClient(settings)
    started = time.perf_counter()
    first_ms = 0
    total = 0
    async for chunk in stream.stream("你好，这是 WebSocket 流式语音测试。"):
        if first_ms == 0:
            first_ms = int((time.perf_counter() - started) * 1000)
        total += len(chunk)
    await stream.close()
    info("tts ws", f"first={first_ms}ms total={int((time.perf_counter() - started) * 1000)}ms")
    ok &= check(first_ms > 0 and first_ms < 2500, "tts ws first packet", f"{first_ms}ms")
    ok &= check(total > 10_000, "tts ws audio", f"{total} bytes")
    await client.close()
    return ok


if __name__ == "__main__":
    run(main)
