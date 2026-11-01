"""OpenViking smoke: identities, namespace isolation, sessions."""

from __future__ import annotations

from general_personal_agent.clients.ov import OpenVikingClient, OVError
from general_personal_agent.settings import Settings
from scripts._common import check, info, run


async def main(settings: Settings) -> bool:
    users = {
        "alice": (settings.ov_user_key_alice, settings.ov_user_alice),
        "bob": (settings.ov_user_key_bob, settings.ov_user_bob),
        "admin": (settings.ov_user_key_admin, settings.ov_user_admin),
    }
    clients = {
        name: OpenVikingClient(settings.ov_url, key, settings.ov_timeout_s)
        for name, (key, _) in users.items()
    }
    ok = True
    try:
        for name, (_, ov_user) in users.items():
            status = await clients[name].status()
            ok &= check(status.get("user") == ov_user, f"{name} identity", str(status.get("user")))
            entries = await clients[name].ls(f"viking://user/{ov_user}")
            ok &= check(isinstance(entries, list), f"{name} own namespace", f"{len(entries)} entries")

        for src, dst in (("alice", "bob"), ("bob", "alice")):
            try:
                await clients[src].ls(f"viking://user/{users[dst][1]}")
                ok &= check(False, f"{src} cannot read {dst}", "unexpected 200")
            except OVError as exc:
                ok &= check(exc.status == 403, f"{src} cannot read {dst}", f"{exc.status} {exc.code}")

        session_id = await clients["alice"].create_session(settings.ov_peer_alice)
        info("ov session", session_id)
        await clients["alice"].add_message(session_id, "user", "smoke: 会话链路")
        context = await clients["alice"].session_context(session_id)
        ok &= check(bool(context.get("messages")), "session context roundtrip")
        await clients["alice"].delete_session(session_id)
    finally:
        for client in clients.values():
            await client.close()
    return ok


if __name__ == "__main__":
    run(main)
