"""Small stdlib-only primitives shared across GPA modules.

This module deliberately imports nothing from ``general_personal_agent`` so it
can be used by any GPA package without creating an import cycle.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any


def to_iso(value: Any) -> str | None:
    """Serialize a value to a UTC ISO-8601 string with a trailing ``Z``.

    Already-serialized strings (OV/DB ``modTime`` values) are passed through
    verbatim. ``None`` stays ``None``; anything else is stringified.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return str(value)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
