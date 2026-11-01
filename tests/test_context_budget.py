"""Context budget must describe and bound the rendered prompt."""

from __future__ import annotations

from general_personal_agent.gpa.context.assembler import (
    estimate_tokens,
    fit_packet_to_budget,
    render_context_message,
)
from general_personal_agent.gpa.schemas import (
    ArtifactContextItem,
    ContextPacket,
    RecentTurn,
    TaskContextItem,
    TokenBudget,
)


def _packet(items: int = 20, snippet: int = 1200) -> ContextPacket:
    return ContextPacket(
        turn_id="t1",
        conversation_id="c1",
        channel="text",
        user_text="总结一下",
        session_summary="s" * 400,
        recent_turns=[RecentTurn(role="user", text="hi", ts="2026-01-01T00:00:00Z")],
        long_term_memory=[
            {"text": f"memory-{i}-" + "x" * snippet, "memoryTypeLabel": "偏好"}
            for i in range(items)
        ],
        personal_kb=[
            {"title": f"kb-{i}", "snippet": "y" * snippet, "scope": "personal"}
            for i in range(items)
        ],
        shared_kb=[
            {"title": f"shared-{i}", "snippet": "z" * snippet, "scope": "shared"}
            for i in range(items)
        ],
        skills=[{"name": f"skill-{i}", "snippet": "w" * snippet} for i in range(items)],
        task_context=[
            TaskContextItem(task_id=f"task-{i}", status="running", summary="q" * 800)
            for i in range(items)
        ],
        attachments=[ArtifactContextItem(artifact_id=f"art-{i}", name=f"f-{i}.png") for i in range(items)],
        token_budget=TokenBudget(total=5000),
    )


def test_fit_packet_reports_actual_rendered_tokens():
    packet = _packet()
    fitted = fit_packet_to_budget(packet, 900, 0.75)
    rendered = render_context_message(fitted)
    actual = estimate_tokens(rendered, 0.75)
    assert fitted.token_budget.used == actual
    assert fitted.token_budget.total == 900
    assert actual <= 900, (actual, rendered[:200])
    assert len(fitted.long_term_memory) < 20 or len(fitted.long_term_memory[0]["text"]) < 1200
    # The original packet is not mutated.
    assert len(packet.long_term_memory) == 20


def test_small_packet_is_unchanged_in_meaning():
    packet = _packet(items=1, snippet=4)
    fitted = fit_packet_to_budget(packet, 5000, 0.75)
    rendered = render_context_message(fitted)
    assert "memory-0-xxxx" in rendered
    assert fitted.token_budget.used == estimate_tokens(rendered, 0.75)
