"""Initialize a fresh GPA OpenViking instance (idempotent).

OpenViking already ships the directory taxonomy GPA relies on
(``agent/``, ``resources/``, ``user/<user>/{memories,peers,resources,skills,
sessions,settings,privacy}``).  This script only creates the *typed*
subdirectories GPA writes into:

    viking://resources/                        shared knowledge (the scope IS the library)
    viking://user/<admin>/resources/uploads/   personal uploads
    viking://user/<admin>/resources/artifacts/ task summary cards

Shared knowledge has no per-role folder (no ``admin/``, no ``shared/``):
admins write ``viking://resources`` directly and every user reads it.

Memory directories (``memories/<type>/``) are created by OpenViking itself on
the first session commit, so they are intentionally not pre-created here.

Usage:
    .venv/bin/python scripts/init_ov_instance.py
"""

from __future__ import annotations

import asyncio

from general_personal_agent.clients.ov import OpenVikingClient, OVError
from general_personal_agent.gpa.context.layout import (
    MEMORY_TYPES,
    OvLayout,
    parse_segments,
)
from general_personal_agent.settings import get_settings


async def _ensure_dir(client: OpenVikingClient, uri: str) -> None:
    try:
        await client.mkdir(uri)
        print(f"created {uri}")
    except OVError as exc:
        message = str(exc).lower()
        if "already exists" in message or "conflict" in message:
            print(f"exists  {uri}")
            return
        raise


def _layout(settings) -> OvLayout:
    return OvLayout(
        ov_user=settings.ov_user_admin or "default",
        personal_kb_indexes=parse_segments(settings.ov_personal_kb_indexes, name="ov_personal_kb_indexes"),
        shared_kb_categories=parse_segments(settings.ov_shared_kb_categories, name="ov_shared_kb_categories"),
    )


async def main() -> None:
    settings = get_settings()
    layout = _layout(settings)
    client = OpenVikingClient(settings.ov_url, settings.ov_key("OV_USER_KEY_ADMIN"), 30.0)
    try:
        # The hosted service pre-creates viking://resources; tolerate that case.
        targets = (
            layout.shared_root,
            layout.write_shared_kb(),
            layout.write_personal_kb("uploads"),
            layout.write_personal_kb("artifacts"),
        )
        for uri in dict.fromkeys(targets):  # write_shared_kb() == shared_root by default
            await _ensure_dir(client, uri)

        _print_layout(settings, layout)
        for index_uri in (layout.shared_root, layout.memories):
            try:
                items = await client.ls(index_uri)
            except OVError as exc:
                print(f"skip {index_uri}: {exc}")
                continue
            print(f"{index_uri}: {len(items)} entrie(s)")
            for item in items[:10]:
                print(f"  - {item.get('uri')}")
    finally:
        await client.close()


def _print_layout(settings, layout: OvLayout) -> None:
    print("\nOpenViking layout used by GPA:")
    print(f"  {layout.agent_skills}/            shared skills (read)")
    print(f"  {layout.shared_root}/<category>/     shared KB (write: {layout.write_shared_kb()})")
    print(f"  {layout.user_root}/")
    print(f"    memories/<type>/<subject>/       self memory ({', '.join(MEMORY_TYPES)})")
    print("    peers/<peer>/memories/           peer memory (VeADK-compatible)")
    print(f"    resources/<index>/               personal KB (write: {layout.write_personal_kb()})")
    if layout.ov_user == settings.ov_user_admin:
        print("    skills/  sessions/  settings/  privacy/  (OpenViking-managed)")


if __name__ == "__main__":
    asyncio.run(main())
