"""Application identity -> OpenViking account/user/peer."""

from __future__ import annotations

from dataclasses import dataclass

from general_personal_agent.db import Database
from general_personal_agent.settings import Settings


class IdentityError(RuntimeError):
    pass


@dataclass(frozen=True)
class Identity:
    tenant_id: str
    app_user_id: str
    ov_account: str
    ov_user: str
    ov_peer: str | None
    key_ref: str
    api_key: str
    role: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


class IdentityService:
    def __init__(self, settings: Settings, db: Database):
        self.settings = settings
        self.db = db

    def _build(self, row: dict) -> Identity:
        api_key = self.settings.ov_key(row.get("key_ref") or "")
        if not api_key:
            raise IdentityError(f"no OpenViking key for {row['app_user_id']} (key_ref={row.get('key_ref')})")
        return Identity(
            tenant_id=row["tenant_id"],
            app_user_id=row["app_user_id"],
            ov_account=row["ov_account"],
            ov_user=row["ov_user"],
            ov_peer=row.get("ov_peer"),
            key_ref=row["key_ref"],
            api_key=api_key,
            role=row.get("role") or "user",
        )

    async def resolve(self, app_user_id: str, tenant_id: str | None = None) -> Identity:
        row = await self.db.get_identity(tenant_id or self.settings.ov_tenant_id, app_user_id)
        if not row or row["status"] != "active":
            raise IdentityError(f"identity not found for {app_user_id}")
        return self._build(row)

    async def for_admin(self, tenant_id: str | None = None) -> Identity:
        # The tenant argument matters once a deployment serves more than one
        # tenant; default to the configured tenant for the single-tenant case.
        return await self.resolve("admin", tenant_id)
