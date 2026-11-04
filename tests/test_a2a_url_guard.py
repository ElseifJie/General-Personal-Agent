"""SSRF guard for A2A outbound URLs."""

from __future__ import annotations

import pytest

from general_personal_agent.gpa.task.url_guard import OutboundUrlError, validate_outbound_url

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _public_resolver(host: str) -> list[str]:
    return ["93.184.216.34"]


async def _metadata_resolver(host: str) -> list[str]:
    return ["169.254.169.254"]


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8080/rpc",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.5/rpc",
        "http://192.168.1.1/rpc",
        "http://172.16.0.1/rpc",
        "http://[::1]/rpc",
        "http://[fd00::1]/rpc",
        "file:///etc/passwd",
        "ftp://public.example.com/rpc",
        "http://user:pass@public.example.com/rpc",
        "http:///rpc",
        "",
    ],
)
async def test_rejects_non_public_or_non_http_targets(url):
    with pytest.raises(OutboundUrlError):
        await validate_outbound_url(url, resolver=_public_resolver)


async def test_rejects_hostname_resolving_to_private_address():
    with pytest.raises(OutboundUrlError, match="non-public"):
        await validate_outbound_url("https://metadata.internal/rpc", resolver=_metadata_resolver)


async def test_dns_failure_fails_closed():
    async def broken(host: str) -> list[str]:
        raise OSError("no such host")

    with pytest.raises(OutboundUrlError, match="cannot resolve"):
        await validate_outbound_url("https://unknown.example/rpc", resolver=broken)


async def test_allowlist_restricts_host_without_dns():
    with pytest.raises(OutboundUrlError, match="not in A2A_ALLOWED_HOSTS"):
        await validate_outbound_url("https://evil.example/rpc", ("a2a.test",), resolver=_public_resolver)
    assert await validate_outbound_url("https://a2a.test/rpc", ("a2a.test",)) == "https://a2a.test/rpc"


async def test_allowlist_allows_subdomain():
    url = "https://rpc.a2a.test/x"
    assert await validate_outbound_url(url, ("a2a.test",)) == url


async def test_accepts_public_host():
    url = "https://public.example/rpc"
    assert await validate_outbound_url(url, resolver=_public_resolver) == url


async def test_executor_rejects_private_artifact_source():
    """A remote agent cannot make the server fetch cloud metadata as an artifact."""
    from types import SimpleNamespace

    from general_personal_agent.gpa.task.a2a import A2aExecutor

    settings = SimpleNamespace(a2a_allowed_hosts="a2a.test", task_artifact_ttl_days=7)
    executor = A2aExecutor(settings, None, tos=None)
    artifacts = [
        {
            "parts": [
                {
                    "type": "file",
                    "file": {
                        "name": "x.txt",
                        "mimeType": "text/plain",
                        "uri": "http://169.254.169.254/latest/meta-data/",
                    },
                }
            ]
        }
    ]
    with pytest.raises(OutboundUrlError, match="169.254.169.254"):
        await executor._artifacts("task-1", artifacts)
