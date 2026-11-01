"""The production parse path really executes in a separate process."""

from __future__ import annotations

import pytest

from general_personal_agent.gpa.files.jobs import _KillableParsePool

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.mark.integration
async def test_killable_parse_pool_returns_result():
    pool = _KillableParsePool(workers=1, memory_mb=512)
    try:
        result = await pool.run("a.txt", "text/plain", b"hello parse", timeout_s=20)
        assert "hello parse" in result.text
    finally:
        await pool.close()
