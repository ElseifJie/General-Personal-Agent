"""Minimal process-wide services for the GPA runtime.

Legacy Wb/Task/Memory/monitor services were removed in P5. What remains is the
shared infrastructure needed by auth, GPA context, outbox and task execution.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from general_personal_agent.clients.ma import MAClient
from general_personal_agent.clients.ov import OpenVikingClient
from general_personal_agent.clients.search import SearchService
from general_personal_agent.clients.tos import TOSClient
from general_personal_agent.clients.tts import TTSClient
from general_personal_agent.db import Database
from general_personal_agent.identity import IdentityService
from general_personal_agent.outbox import OutboxWorker
from general_personal_agent.settings import Settings


@dataclass
class Services:
    settings: Settings
    db: Database
    tos: TOSClient
    ma: MAClient
    search: SearchService
    tts_client: TTSClient
    identity_service: IdentityService
    outbox: OutboxWorker | None = None
    _ov_clients: dict = field(default_factory=dict)

    def tts(self) -> TTSClient:
        return self.tts_client

    async def ov_client_for(self, app_user_id: str) -> OpenVikingClient:
        if app_user_id not in self._ov_clients:
            identity = await self.identity_service.resolve(app_user_id)
            self._ov_clients[app_user_id] = OpenVikingClient(
                self.settings.ov_url, identity.api_key, self.settings.ov_timeout_s
            )
        return self._ov_clients[app_user_id]

    async def close(self) -> None:
        for client in self._ov_clients.values():
            try:
                await client.close()
            except Exception:  # noqa: BLE001
                pass
        self._ov_clients.clear()


def build_outbox_handlers(services: Services) -> dict:
    async def ov_message(payload: dict) -> None:
        client = await services.ov_client_for(payload["app_user_id"])
        await client.add_message(payload["session_id"], payload["role"], payload["content"])

    async def ov_commit(payload: dict) -> None:
        client = await services.ov_client_for(payload["app_user_id"])
        await client.commit_session(payload["session_id"])

    return {"ov.message": ov_message, "ov.commit": ov_commit}


def build_services(
    settings: Settings,
    db: Database,
    tos: TOSClient,
    ma: MAClient,
    search: SearchService,
    tts: TTSClient,
) -> Services:
    services = Services(
        settings=settings,
        db=db,
        tos=tos,
        ma=ma,
        search=search,
        tts_client=tts,
        identity_service=IdentityService(settings, db),
    )
    services.outbox = OutboxWorker(
        db,
        build_outbox_handlers(services),
        interval_s=settings.outbox_interval_s,
        batch_size=settings.outbox_batch_size,
        max_attempts=settings.outbox_max_attempts,
    )
    return services
