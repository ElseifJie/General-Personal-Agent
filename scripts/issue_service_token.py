"""Issue a short-lived service token for the internal/MCP task gateway."""

from __future__ import annotations

import argparse
import json

from general_personal_agent.gpa.service_token import ALL_SCOPES, issue_service_token
from general_personal_agent.settings import get_settings


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant", default="demo")
    parser.add_argument("--subject", default="local-worker")
    parser.add_argument("--scopes", default=",".join(ALL_SCOPES))
    parser.add_argument("--users", default="", help="comma separated app user IDs")
    parser.add_argument("--ttl", type=int, default=0)
    args = parser.parse_args()
    scopes = tuple(item.strip() for item in args.scopes.split(",") if item.strip())
    result = issue_service_token(
        get_settings(),
        tenant_id=args.tenant,
        subject=args.subject,
        scopes=scopes,
        ttl_s=args.ttl or None,
        allowed_users=tuple(item.strip() for item in args.users.split(",") if item.strip()),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
