"""Managed Agents smoke: agent/environment/sessions/files."""

from __future__ import annotations

import httpx

from general_personal_agent.settings import Settings
from scripts._common import check, info, run


async def main(settings: Settings) -> bool:
    headers = {"Authorization": f"Bearer {settings.ma_key}"}
    base = settings.ma_base_url.rstrip("/")
    ok = True
    async with httpx.AsyncClient(timeout=30.0) as client:
        agent = await client.get(f"{base}/agents/{settings.ma_agent_id}", headers=headers)
        ok &= check(agent.status_code == 200, "MA agent", str(agent.status_code))
        env = await client.get(f"{base}/environments/{settings.ma_environment_id}", headers=headers)
        ok &= check(env.status_code == 200, "MA environment", str(env.status_code))
        sessions = await client.get(f"{base}/sessions", headers=headers, params={"limit": 1})
        ok &= check(sessions.status_code == 200, "MA sessions list", str(sessions.status_code))
        files = await client.get(f"{base}/files", headers=headers, params={"limit": 1})
        ok &= check(files.status_code == 200, "MA files list", str(files.status_code))
    info("agent", settings.ma_agent_id)
    info("environment", settings.ma_environment_id)
    return ok


if __name__ == "__main__":
    run(main)
