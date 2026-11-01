"""OpenViking HTTP client. One instance per end user (key determines identity)."""

from __future__ import annotations

from typing import Any

import httpx


class OVError(RuntimeError):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(f"{code} ({status}): {message}")
        self.status = status
        self.code = code
        self.message = message


class OpenVikingClient:
    def __init__(self, base_url: str, api_key: str, timeout: float = 10.0):
        if not api_key:
            raise ValueError("OpenViking api_key is empty")
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"X-API-Key": api_key},
            timeout=timeout,
        )

    async def __aenter__(self) -> OpenVikingClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def close(self) -> None:
        await self._http.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        resp = await self._http.request(method, path, **kwargs)
        try:
            payload = resp.json()
        except ValueError:
            payload = {}
        if resp.is_success and payload.get("status") != "error":
            return payload.get("result")
        err = payload.get("error") or {}
        raise OVError(resp.status_code, err.get("code", "HTTP_ERROR"), err.get("message", resp.text[:200]))

    async def status(self) -> dict:
        return await self._request("GET", "/api/v1/system/status")

    async def ls(self, uri: str) -> list[dict]:
        return await self._request("GET", "/api/v1/fs/ls", params={"uri": uri}) or []

    async def tree(
        self,
        uri: str = "viking://",
        *,
        level_limit: int = 12,
        node_limit: int = 2000,
    ) -> list[dict]:
        """Returns the visible subtree as a flat list of entries.

        OpenViking's ``fs/tree`` walks the whole scope with the caller's key, so
        the result is exactly the directory structure that account may see.
        """
        return (
            await self._request(
                "GET",
                "/api/v1/fs/tree",
                params={
                    "uri": uri,
                    "level_limit": level_limit,
                    "node_limit": node_limit,
                    "output": "original",
                },
            )
            or []
        )

    async def mkdir(self, uri: str) -> dict:
        return await self._request("POST", "/api/v1/fs/mkdir", json={"uri": uri})

    async def delete(self, uri: str, recursive: bool = True) -> dict:
        return await self._request(
            "DELETE",
            "/api/v1/fs",
            params={"uri": uri, "recursive": "true" if recursive else "false"},
        )

    async def stat(self, uri: str) -> dict:
        return await self._request("GET", "/api/v1/fs/stat", params={"uri": uri}) or {}

    async def reindex(self, uri: str) -> dict:
        """Ask OV to rebuild the node summary; not all deployments expose it."""
        return await self._request("POST", "/api/v1/content/reindex", json={"uri": uri}) or {}

    async def read(self, uri: str) -> str:
        return await self._request("GET", "/api/v1/content/read", params={"uri": uri})

    async def abstract(self, uri: str) -> str:
        return await self._request("GET", "/api/v1/content/abstract", params={"uri": uri})

    async def search(
        self,
        query: str,
        target_uri: str | list[str] | None = None,
        limit: int = 8,
        context_type: str | list[str] | None = None,
    ) -> dict:
        body: dict[str, Any] = {"query": query, "limit": limit}
        if target_uri:
            body["target_uri"] = target_uri
        if context_type:
            body["context_type"] = context_type
        return await self._request("POST", "/api/v1/search/find", json=body) or {}

    async def commit_session(self, session_id: str) -> dict:
        return await self._request("POST", f"/api/v1/sessions/{session_id}/commit")

    async def create_session(self, actor_peer_id: str | None = None) -> str:
        body = {"actor_peer_id": actor_peer_id} if actor_peer_id else {}
        result = await self._request("POST", "/api/v1/sessions", json=body)
        return result["session_id"]

    async def add_message(self, session_id: str, role: str, content: str) -> dict:
        return await self._request(
            "POST",
            f"/api/v1/sessions/{session_id}/messages",
            json={"role": role, "content": content},
        )

    async def session_context(self, session_id: str) -> dict:
        return await self._request("GET", f"/api/v1/sessions/{session_id}/context")

    async def delete_session(self, session_id: str) -> dict:
        return await self._request("DELETE", f"/api/v1/sessions/{session_id}")

    async def import_bytes(
        self,
        filename: str,
        data: bytes,
        to_uri: str,
        content_type: str = "text/markdown",
        wait: bool = True,
    ) -> dict:
        uploaded = await self._request(
            "POST",
            "/api/v1/resources/temp_upload",
            files={"file": (filename, data, content_type)},
        )
        return await self._request(
            "POST",
            "/api/v1/resources",
            json={"temp_file_id": uploaded["temp_file_id"], "to": to_uri, "wait": wait},
        )

    async def task(self, task_id: str) -> dict:
        return await self._request("GET", f"/api/v1/tasks/{task_id}")
