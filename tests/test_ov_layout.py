"""OpenViking directory layout: the single source of truth for viking:// URIs."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from general_personal_agent.gpa.context.layout import (
    MEMORY_TYPE_LABELS,
    MEMORY_TYPES,
    SCOPE_AGENT_SKILL,
    SCOPE_PEER_MEMORY,
    SCOPE_PERSONAL_KB,
    SCOPE_PRIVACY,
    SCOPE_SELF_MEMORY,
    SCOPE_SESSION,
    SCOPE_SETTINGS,
    SCOPE_SHARED_KB,
    SCOPE_USER_SKILL,
    OvLayout,
    build_layout,
    parse_segments,
    safe_segment,
)


def _layout(**kwargs) -> OvLayout:
    return OvLayout(ov_user="alice", ov_peer="device_alice", **kwargs)


def test_scope_directories_follow_openviking_taxonomy():
    layout = _layout()
    assert layout.user_root == "viking://user/alice"
    assert layout.memories == "viking://user/alice/memories"
    assert layout.peers == "viking://user/alice/peers"
    assert layout.resources == "viking://user/alice/resources"
    assert layout.skills == "viking://user/alice/skills"
    assert layout.sessions == "viking://user/alice/sessions"
    assert layout.privacy == "viking://user/alice/privacy"
    assert layout.settings_uri == "viking://user/alice/settings/user_config.json"
    assert layout.agent_skills == "viking://agent/skills"
    assert layout.agent_tools == "viking://agent/tools"
    assert layout.shared_root == "viking://resources"


def test_memory_type_buckets_cover_ov_default_types():
    layout = _layout()
    assert layout.memory_type("preferences") == "viking://user/alice/memories/preferences"
    assert set(MEMORY_TYPES) == {
        "profile",
        "preferences",
        "entities",
        "events",
        "cases",
        "experiences",
        "trajectories",
    }
    assert set(MEMORY_TYPE_LABELS) == set(MEMORY_TYPES)


def test_memory_roots_include_peers_only_with_a_peer_identity():
    layout = _layout()
    assert layout.memory_roots() == [layout.memories, layout.peers]
    assert layout.memory_roots(include_peers=False) == [layout.memories]

    without_peer = OvLayout(ov_user="bob")
    assert without_peer.memory_roots() == ["viking://user/bob/memories"]
    with pytest.raises(ValueError):
        without_peer.peer_memories()


def test_kb_roots_default_to_scope_roots_and_narrow_to_indexes():
    broad = _layout()
    assert broad.personal_kb_roots() == ["viking://user/alice/resources"]
    assert broad.shared_kb_roots() == ["viking://resources"]
    assert broad.write_personal_kb() == "viking://user/alice/resources/uploads"
    # The shared scope is the library: no per-role admin/ or shared/ wrapper.
    assert broad.write_shared_kb() == "viking://resources"
    assert broad.write_shared_kb() == broad.shared_root

    narrow = _layout(personal_kb_indexes=("uploads", "artifacts"), shared_kb_categories=("ops",))
    assert narrow.personal_kb_roots() == [
        "viking://user/alice/resources/uploads",
        "viking://user/alice/resources/artifacts",
    ]
    assert narrow.shared_kb_roots() == ["viking://resources/ops"]
    assert narrow.write_shared_kb() == "viking://resources/ops"
    assert narrow.artifact_card("task_1") == ("viking://user/alice/resources/artifacts/task_1.md")


def test_scope_classification_matches_ov_directories():
    layout = _layout()
    assert layout.scope_of("viking://user/alice/memories/entities/x/y.md") == SCOPE_SELF_MEMORY
    assert (
        layout.scope_of("viking://user/alice/peers/device_alice/memories/entities/x/y.md")
        == SCOPE_PEER_MEMORY
    )
    assert layout.scope_of("viking://user/alice/peers/device_alice") == "user"
    assert layout.scope_of("viking://user/alice/resources/uploads/a.pdf") == SCOPE_PERSONAL_KB
    assert layout.scope_of("viking://resources/admin/c.md") == SCOPE_SHARED_KB
    assert layout.scope_of("viking://agent/skills/weekly/SKILL.md") == SCOPE_AGENT_SKILL
    assert layout.scope_of("viking://user/alice/skills/x/SKILL.md") == SCOPE_USER_SKILL
    assert layout.scope_of("viking://user/alice/sessions/s1/messages.jsonl") == SCOPE_SESSION
    assert layout.scope_of("viking://user/alice/privacy/x") == SCOPE_PRIVACY
    assert layout.scope_of("viking://user/alice/settings/user_config.json") == SCOPE_SETTINGS
    assert layout.scope_of("viking://elsewhere/x") == "unknown"


def test_memory_type_and_peer_extraction():
    layout = _layout()
    self_memory = "viking://user/alice/memories/preferences/user/color.md"
    peer_memory = "viking://user/alice/peers/device_alice/memories/entities/li/wei.md"
    assert layout.memory_type_of(self_memory) == "preferences"
    assert layout.peer_of(self_memory) is None
    assert layout.memory_type_of(peer_memory) == "entities"
    assert layout.peer_of(peer_memory) == "device_alice"
    # A peer directory that is not a memory bucket is not memory.
    assert layout.memory_type_of("viking://user/alice/peers/device_alice/notes/a.md") is None
    assert layout.memory_type_of("viking://resources/admin/a.md") is None


def test_segments_are_validated_before_becoming_uris():
    assert safe_segment("uploads", name="index") == "uploads"
    for bad in ("", "..", "a/b", "a b"):
        with pytest.raises(ValueError):
            safe_segment(bad, name="index")
    assert parse_segments("", name="idx") == ()
    assert parse_segments(" uploads, artifacts ,uploads", name="idx") == ("uploads", "artifacts")
    with pytest.raises(ValueError):
        parse_segments("uploads,../etc", name="idx")


def test_build_layout_reads_settings_and_identity():
    settings = SimpleNamespace(
        ov_personal_kb_indexes="uploads,artifacts",
        ov_shared_kb_categories="admin",
    )
    identity = SimpleNamespace(ov_user="alice", ov_peer="device_alice")
    layout = build_layout(settings, identity)
    assert layout.ov_user == "alice"
    assert layout.ov_peer == "device_alice"
    assert layout.personal_kb_indexes == ("uploads", "artifacts")
    assert layout.shared_kb_categories == ("admin",)
