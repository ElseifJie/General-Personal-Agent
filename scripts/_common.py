"""Shared helpers for smoke scripts."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Awaitable, Callable

from general_personal_agent.settings import Settings, get_settings

PASS, FAIL, WARN = "PASS", "FAIL", "WARN"


def check(cond: bool, label: str, detail: str = "") -> bool:
    print(f"[{PASS if cond else FAIL}] {label}" + (f" — {detail}" if detail else ""))
    return cond


def info(label: str, detail: str = "") -> None:
    print(f"[INFO] {label}" + (f" — {detail}" if detail else ""))


def warn(label: str, detail: str = "") -> None:
    print(f"[{WARN}] {label}" + (f" — {detail}" if detail else ""))


def run(main: Callable[[Settings], Awaitable[bool]]) -> None:
    settings = get_settings()
    ok = asyncio.run(main(settings))
    sys.exit(0 if ok else 1)
