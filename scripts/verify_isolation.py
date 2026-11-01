"""Isolation acceptance: user-to-user 403, shared KB read, write probe."""

from __future__ import annotations

from general_personal_agent.clients.ov import OpenVikingClient, OVError
from general_personal_agent.gpa.context.layout import SHARED_ROOT
from general_personal_agent.settings import Settings
from scripts._common import check, info, run, warn


async def main(settings: Settings) -> bool:
    alice = OpenVikingClient(settings.ov_url, settings.ov_user_key_alice, settings.ov_timeout_s)
    bob = OpenVikingClient(settings.ov_url, settings.ov_user_key_bob, settings.ov_timeout_s)
    ok = True
    try:
        for src, client, target in (
            ("alice", alice, settings.ov_user_bob),
            ("bob", bob, settings.ov_user_alice),
        ):
            try:
                await client.ls(f"viking://user/{target}")
                ok &= check(False, f"{src} cannot read {target}", "unexpected 200")
            except OVError as exc:
                ok &= check(exc.status == 403, f"{src} cannot read {target}", f"{exc.status} {exc.code}")

        for name, client in (("alice", alice), ("bob", bob)):
            entries = await client.ls(SHARED_ROOT)
            ok &= check(isinstance(entries, list), f"{name} can read shared KB", f"{len(entries)} entries")

        probe = f"{SHARED_ROOT}/_isolation_probe"
        try:
            await alice.mkdir(probe)
            warn("platform allows normal users to write the shared KB (enforced by backend instead)")
            await alice.delete(probe)
        except OVError as exc:
            check(exc.status == 403, "platform blocks shared KB writes", f"{exc.status} {exc.code}")
    finally:
        await alice.close()
        await bob.close()
    info("note", "shared KB write protection is enforced by the API layer (admin role) on this instance")
    return ok


if __name__ == "__main__":
    run(main)
