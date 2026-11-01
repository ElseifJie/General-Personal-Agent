"""Ark chat smoke (thinking disabled)."""

from __future__ import annotations

import httpx

from general_personal_agent.settings import Settings
from scripts._common import check, info, run


async def main(settings: Settings) -> bool:
    body = {
        "model": settings.model_agent_name,
        "messages": [{"role": "user", "content": "只回复：ok"}],
        "max_tokens": 16,
        "thinking": {"type": "disabled"},
    }
    async with httpx.AsyncClient(timeout=40.0) as client:
        resp = await client.post(
            f"{settings.ark_base_url}/chat/completions",
            headers={"Authorization": f"Bearer {settings.ark_api_key}"},
            json=body,
        )
    info("model", settings.model_agent_name)
    ok = check(resp.status_code == 200, "ark chat", str(resp.status_code))
    if ok:
        text = resp.json()["choices"][0]["message"].get("content", "")
        ok &= check(bool(text.strip()), "ark reply", repr(text))
    return ok


if __name__ == "__main__":
    run(main)
