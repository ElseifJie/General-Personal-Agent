"""Run the GPA Task Gateway as an MCP Streamable HTTP server.

    python -m scripts.task_mcp_server --host 127.0.0.1 --port 8201

Callers send a service JWT in `X-GPA-Service-Token` plus
`X-GPA-App-User-Id` / `X-GPA-Conversation-Id`; see gpa/task/mcp_server.py.
"""

from __future__ import annotations

import argparse

from general_personal_agent.clients.ma import MAClient
from general_personal_agent.clients.tos import TOSClient
from general_personal_agent.db import Database
from general_personal_agent.gpa.services import build_gpa_services
from general_personal_agent.gpa.task.mcp_server import build_task_mcp
from general_personal_agent.identity import IdentityService
from general_personal_agent.settings import get_settings


async def _build_gpa():
    settings = get_settings()
    db = await Database.connect(settings)
    ma = MAClient(settings.ma_base_url, settings.ma_key)
    tos = TOSClient(
        settings.tos_endpoint,
        settings.database_tos_region,
        settings.volcengine_access_key,
        settings.volcengine_secret_key,
        settings.database_tos_bucket_name,
        session_token=settings.volcengine_session_token,
        prefix=settings.tos_prefix,
    )
    gpa = build_gpa_services(
        settings,
        db,
        ma=ma,
        tos=tos,
        identity_service=IdentityService(settings, db),
    )
    return db, gpa


def main() -> None:

    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8201)
    parser.add_argument("--path", default="/mcp")
    parser.add_argument("--ssl-certfile", default="")
    parser.add_argument("--ssl-keyfile", default="")
    parser.add_argument("--ssl-ca-certs", default="")
    parser.add_argument("--ssl-cert-reqs", type=int, default=2)
    args = parser.parse_args()
    settings = get_settings()
    state: dict = {}

    async def get_gpa():
        if "gpa" not in state:
            state["db"], state["gpa"] = await _build_gpa()
        return state["gpa"]

    mcp = build_task_mcp(get_gpa, settings)
    run_kwargs = {}
    if args.ssl_certfile and args.ssl_keyfile:
        run_kwargs["uvicorn_config"] = {
            "ssl_certfile": args.ssl_certfile,
            "ssl_keyfile": args.ssl_keyfile,
            "ssl_ca_certs": args.ssl_ca_certs or None,
            "ssl_cert_reqs": args.ssl_cert_reqs,
        }
    mcp.run(transport="http", host=args.host, port=args.port, path=args.path, **run_kwargs)


if __name__ == "__main__":
    main()
