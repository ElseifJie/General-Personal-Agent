"""Files/KB rollout gate.

``gpa_files_rollout`` is enforced for every file/KB HTTP entry point:
``off`` disables the surface, ``internal`` allows the allowlist (and admins),
``percent`` uses a stable per-user bucket, ``all`` enables everyone.
"""

from __future__ import annotations

import hashlib


def _allowlist(raw: str | None) -> set[str]:
    return {part.strip() for part in str(raw or "").split(",") if part.strip()}


def files_rollout_allows(settings, app_user_id: str, role: str = "user") -> bool:
    mode = str(getattr(settings, "gpa_files_rollout", "all") or "all").strip().lower()
    if mode in {"all", "on", "true", "100"}:
        return True
    if mode in {"off", "none", "false", "0"}:
        return False
    if app_user_id in _allowlist(getattr(settings, "gpa_files_allowlist", "")):
        return True
    if mode == "internal":
        return role == "admin"
    if mode == "percent":
        stable = hashlib.sha256(app_user_id.encode()).hexdigest()[:8]
        bucket = int(stable, 16) % 100
        percent = int(getattr(settings, "gpa_files_rollout_percent", 0) or 0)
        return bucket < max(0, min(100, percent))
    return False
