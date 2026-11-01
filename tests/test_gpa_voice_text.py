"""Voice-only text shaping: filler selection and TTS sanitising."""

from __future__ import annotations

from general_personal_agent.gpa.agent.prompt import build_instruction
from general_personal_agent.gpa.voice.fillers import (
    FILLER_REALTIME,
    FILLER_RETRIEVAL,
    FILLER_TASK,
    filler_for,
    tool_filler,
    tool_label,
)
from general_personal_agent.gpa.voice.tts_text import sanitize_for_tts


def test_realtime_question_gets_search_filler():
    assert filler_for("今天深圳天气怎么样") == FILLER_REALTIME
    assert filler_for("帮我查一下明天会下雨吗") == FILLER_REALTIME


def test_task_request_gets_task_filler():
    assert filler_for("帮我做一张海报") == FILLER_TASK
    assert filler_for("生成一份周报文档") == FILLER_TASK


def test_retrieval_gets_memory_filler():
    assert filler_for("你还记得我上次说的偏好吗") == FILLER_RETRIEVAL
    assert filler_for("总结一下刚才的内容") == FILLER_RETRIEVAL


def test_plain_chat_has_no_filler():
    assert filler_for("你好呀") == ""
    assert filler_for("") == ""


def test_tool_progress_labels():
    assert tool_label("web_search") == "正在查资料"
    assert tool_filler("web_search") == "我查一下资料。"
    assert tool_label("unknown_tool") == ""


def test_sanitize_strips_markdown_and_urls():
    raw = (
        "## 深圳天气\n"
        "- **当前**：25℃，[来源](https://example.com/weather)\n"
        "- 详见 https://example.com/a?b=1\n"
        "`code` 和 *重点* 🙂"
    )
    clean = sanitize_for_tts(raw)

    assert "##" not in clean
    assert "**" not in clean
    assert "https://" not in clean
    assert "来源" in clean
    assert "code" in clean
    assert "重点" in clean
    assert "🙂" not in clean
    assert "- " not in clean


def test_sanitize_keeps_plain_chinese():
    assert sanitize_for_tts("今天深圳 25 摄氏度，天气不错。") == "今天深圳 25 摄氏度，天气不错。"


def test_voice_instruction_differs_from_text():
    text = build_instruction("2026-10-27（周二）", channel="text")
    voice = build_instruction("2026-10-27（周二）", channel="voice")

    assert "当前通道：语音" not in text
    assert "当前通道：语音" in voice
    assert "不要 Markdown" in voice
    assert "不要朗读 URL" in voice
