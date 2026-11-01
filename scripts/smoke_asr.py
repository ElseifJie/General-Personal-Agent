"""ASR smoke: TTS roundtrip through the streaming ASR client."""

from __future__ import annotations

from general_personal_agent.audio import pcm_duration_ms, wav_to_pcm16k
from general_personal_agent.clients.asr import StreamingASR
from general_personal_agent.clients.tts import TTSClient
from general_personal_agent.settings import Settings
from scripts._common import check, info, run

SCRIPT = "你好，参数校验通过。"


async def main(settings: Settings) -> bool:
    tts = TTSClient(
        settings.tool_vespeech_app_id,
        settings.tool_vespeech_api_key,
        settings.tool_vespeech_speaker,
        cluster=settings.tts_cluster,
    )
    pcm = wav_to_pcm16k(await tts.synthesize_wav(SCRIPT))
    info("pcm", f"{len(pcm)} bytes / {pcm_duration_ms(pcm)} ms")

    asr = StreamingASR(
        settings.asr_ws_url,
        settings.tool_vespeech_app_id,
        settings.tool_vespeech_api_key,
        sample_rate=settings.asr_sample_rate,
        enable_nonstream=settings.asr_enable_nonstream,
    )
    await asr.start()
    for offset in range(0, len(pcm), 3200):
        await asr.send(pcm[offset : offset + 3200])
    text = await asr.finish()
    info("asr text", text or "(empty)")
    ok = check(bool(text), "asr returned text")
    ok &= check("参数校验" in text, "asr content matches", text[:40])
    return ok


if __name__ == "__main__":
    run(main)
