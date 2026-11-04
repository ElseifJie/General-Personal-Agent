"""Managed Agents (MA) Sessions API client."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable

import httpx

FILE_PAGE_LIMIT = 100
MAX_FILE_PAGES = 20
MAX_EVENT_PAGES = 50


class MAError(RuntimeError):
    pass


class MAStatusError(MAError):
    """HTTP-level failure carrying the status code (429/5xx backoff decisions)."""

    def __init__(self, method: str, path: str, status_code: int, body: str):
        super().__init__(f"MA {method} {path} -> {status_code}: {body[:300]}")
        self.status_code = status_code


class MAClient:
    def __init__(self, base_url: str, api_key: str, timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    async def _request(self, method: str, path: str, **kwargs: object) -> dict:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.request(method, f"{self.base_url}{path}", headers=self._headers, **kwargs)
        if resp.status_code >= 400:
            raise MAStatusError(method, path, resp.status_code, resp.text)
        if not resp.content:
            return {}
        return resp.json()

    async def _request_raw(self, method: str, path: str, **kwargs: object) -> httpx.Response:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.request(method, f"{self.base_url}{path}", headers=self._headers, **kwargs)
        if resp.status_code >= 400:
            raise MAStatusError(method, path, resp.status_code, resp.text)
        return resp

    async def get_agent(self, agent_id: str) -> dict:
        return await self._request("GET", f"/agents/{agent_id}")

    async def get_environment(self, environment_id: str) -> dict:
        return await self._request("GET", f"/environments/{environment_id}")

    async def create_session(
        self,
        agent_id: str,
        environment_id: str,
        resources: list[dict] | None = None,
        vault_ids: list[str] | None = None,
        agent_version: int | None = None,
    ) -> str:
        """Create a session; ``resources`` items are ``{file_id, mount_path}`` dicts.

        ``agent_version`` is the build to run; when omitted the field is left
        out of the request. A fixed old build silently drops tools added later.
        """
        agent: dict = {"type": "agent", "id": agent_id}
        if agent_version is not None:
            agent["version"] = int(agent_version)
        body: dict = {
            "agent": agent,
            "environment_id": environment_id,
        }
        if resources:
            body["resources"] = [
                {"type": "file", "file_id": r["file_id"], "mount_path": r["mount_path"]}
                for r in resources
            ]
        if vault_ids is not None:
            body["vault_ids"] = vault_ids
        data = await self._request("POST", "/sessions", json=body)
        return data["id"]

    async def get_session(self, session_id: str) -> dict:
        return await self._request("GET", f"/sessions/{session_id}")

    async def delete_session(self, session_id: str) -> None:
        await self._request("DELETE", f"/sessions/{session_id}")

    async def send_message(self, session_id: str, text: str) -> dict:
        return await self.submit_event(
            session_id, {"type": "user.message", "content": [{"type": "text", "text": text}]}
        )

    async def submit_event(self, session_id: str, event: dict) -> dict:
        """Submit one event; ``user.interrupt`` 是 ``{"type": "user.interrupt"}``."""
        submitted = (
            event
            if event.get("type") == "user.message"
            else {"type": event.get("type")}
        )
        return await self._request(
            "POST", f"/sessions/{session_id}/events", json={"events": [submitted]}
        )

    async def upload_file(
        self, name: str, data: bytes, content_type: str = "application/octet-stream", purpose: str = "agent"
    ) -> dict:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                f"{self.base_url}/files",
                headers=headers,
                data={"purpose": purpose},
                files={"file": (name, data, content_type)},
            )
        if resp.status_code >= 400:
            raise MAStatusError("POST", "/files", resp.status_code, resp.text)
        return resp.json()

    async def delete_file(self, file_id: str) -> None:
        await self._request("DELETE", f"/files/{file_id}")

    @staticmethod
    def _data_list(payload: object) -> list[dict]:
        """MA sometimes returns ``null`` while an export is still registering."""
        if not isinstance(payload, dict):
            return []
        data = payload.get("data")
        if not isinstance(data, list):
            return []
        return [item for item in data if isinstance(item, dict)]

    async def list_events(self, session_id: str, limit: int = 100, page: str | None = None) -> list[dict]:
        params: dict = {"limit": limit}
        if page:
            params["page"] = page
        data = await self._request("GET", f"/sessions/{session_id}/events", params=params)
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        return self._data_list(data)

    async def fetch_events_all(self, session_id: str) -> list[dict]:
        """Page through ``next_page`` until exhausted; returns every event once.

        Raw HTTP events flatten their payload: ``{id, type, processed_at, ...data}``.
        """
        events: list[dict] = []
        seen_pages: set[str] = set()
        page: str | None = None
        for _ in range(MAX_EVENT_PAGES):
            data = await self._request(
                "GET",
                f"/sessions/{session_id}/events",
                params={"limit": 100, **({"page": page} if page else {})},
            )
            if isinstance(data, list):
                events.extend(item for item in data if isinstance(item, dict))
                return events
            events.extend(self._data_list(data))
            page = (data.get("next_page") if isinstance(data, dict) else None) or None
            if not page:
                return events
            if page in seen_pages:
                raise MAError(f"MA event pagination loop for session {session_id}")
            seen_pages.add(page)
        return events

    async def stream_events(self, session_id: str, idle_timeout_s: float = 60.0) -> AsyncIterator[dict]:
        """Optional SSE stream (``/events/stream``); verified, off by default.

        Yields raw flat events like ``fetch_events_all``. ``idle_timeout_s``:
        无数据（无心跳）超时抛 ``httpx.ReadTimeout`` —— 空会话不会无限挂起。
        """
        headers = {"Authorization": f"Bearer {self.api_key}", "Accept": "text/event-stream"}
        timeout = httpx.Timeout(connect=10.0, read=idle_timeout_s, write=10.0, pool=10.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "GET", f"{self.base_url}/sessions/{session_id}/events/stream", headers=headers
            ) as resp:
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode(errors="replace")
                    raise MAStatusError("GET", f"/sessions/{session_id}/events/stream", resp.status_code, body)
                event_type = None
                async for line in resp.aiter_lines():
                    line = line.rstrip("\n")
                    if not line:
                        continue
                    if line.startswith("event:"):
                        event_type = line.split(":", 1)[1].strip()
                    elif line.startswith("data:"):
                        payload = line.split(":", 1)[1].strip()
                        if payload.startswith("{"):
                            import json

                            yield {"type": event_type or "", **json.loads(payload)}
                        elif event_type and event_type.startswith("session."):
                            yield {"type": event_type}
                        event_type = None

    async def list_files(self, session_id: str) -> list[dict]:
        data = await self._request(
            "GET",
            "/files",
            params={"scope_id": session_id, "purpose": "agent", "limit": 50},
        )
        return self._data_list(data)

    async def list_files_all(self, session_id: str) -> list[dict]:
        """Page through ``after=last_id`` until exhausted (or MAX_FILE_PAGES)."""
        files: list[dict] = []
        after: str | None = None
        for _ in range(MAX_FILE_PAGES):
            params: dict = {"scope_id": session_id, "purpose": "agent", "limit": FILE_PAGE_LIMIT}
            if after:
                params["after"] = after
            data = await self._request("GET", "/files", params=params)
            if data is None:
                return files
            page_items = self._data_list(data) if isinstance(data, dict) else [
                item for item in data if isinstance(item, dict)
            ]
            files.extend(page_items)
            if not isinstance(data, dict) or data.get("has_more") is not True:
                return files
            after = data.get("last_id") or None
            if not after:
                return files
        return files

    async def list_session_resources(self, session_id: str) -> list[dict]:
        data = await self._request("GET", f"/sessions/{session_id}/resources")
        return self._data_list(data)

    async def wait_idle(
        self,
        session_id: str,
        timeout: float,
        poll_interval: float,
        on_events: Callable[[list[dict]], Awaitable[None]] | None = None,
    ) -> list[dict]:
        """Poll until the session reports idle; returns all observed events.

        Events are deduped by id: the old ``len(batch)`` cursor lost events when
        Ark re-paged a >100-event session (the leading items drop off page 1).
        """
        deadline = asyncio.get_running_loop().time() + timeout
        events: list[dict] = []
        seen_ids: set[str] = set()
        while asyncio.get_running_loop().time() < deadline:
            batch = await self.fetch_events_all(session_id)
            new = [e for e in batch if e.get("id") and e["id"] not in seen_ids]
            if new:
                events.extend(new)
                seen_ids.update(e["id"] for e in new)
                if on_events is not None:
                    await on_events(new)
            statuses = {e.get("type") for e in batch}
            if "session.status_idle" in statuses or "session.status_completed" in statuses:
                return events
            await asyncio.sleep(poll_interval)
        raise MAError(f"MA session {session_id} did not finish within {timeout}s")
