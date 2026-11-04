"""Single-active voice session registry."""

from __future__ import annotations

from general_personal_agent.gpa.voice.registry import VoiceSessionRegistry


def test_voice_session_registry_is_single_active():
    registry = VoiceSessionRegistry()
    first = object()
    second = object()
    assert registry.acquire("c1", first) is True
    assert registry.acquire("c1", second) is False
    # A stale release from a non-owner must not evict the active session.
    registry.release("c1", second)
    assert registry.get("c1") is first
    registry.release("c1", first)
    assert registry.get("c1") is None
    assert registry.acquire("c1", second) is True
