"""Files rollout gate is actually enforced."""

from __future__ import annotations

from types import SimpleNamespace

from general_personal_agent.gpa.files.rollout import files_rollout_allows


def _settings(**kwargs):
    values = {"gpa_files_rollout": "all", "gpa_files_allowlist": "", "gpa_files_rollout_percent": 0}
    values.update(kwargs)
    return SimpleNamespace(**values)


def test_rollout_modes():
    assert files_rollout_allows(_settings(gpa_files_rollout="all"), "alice") is True
    assert files_rollout_allows(_settings(gpa_files_rollout="off"), "alice") is False
    assert files_rollout_allows(
        _settings(gpa_files_rollout="internal", gpa_files_allowlist="alice"), "alice"
    ) is True
    assert files_rollout_allows(
        _settings(gpa_files_rollout="internal", gpa_files_allowlist="alice"), "bob"
    ) is False
    assert files_rollout_allows(
        _settings(gpa_files_rollout="internal"), "admin", role="admin"
    ) is True
    assert files_rollout_allows(
        _settings(gpa_files_rollout="percent", gpa_files_rollout_percent=100), "bob"
    ) is True
    assert files_rollout_allows(
        _settings(gpa_files_rollout="percent", gpa_files_rollout_percent=0), "bob"
    ) is False
