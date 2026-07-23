from __future__ import annotations

import asyncio
from collections.abc import Sequence

import pytest

from pdf_api.security import (
    NetworkGuard,
    RenderTimeoutError,
    RequestLimitError,
    Resolver,
    ResponseBytesLimitError,
    SecurityBoundaryError,
    SecurityPolicy,
    run_with_hard_timeout,
    validate_target,
)

PUBLIC_IP = "93.184.216.34"


def resolver_for(*addresses: str) -> Resolver:
    async def _resolver(_host: str, _port: int) -> Sequence[str]:
        return addresses

    return _resolver


@pytest.mark.parametrize(
    "address",
    [
        "10.0.0.1",
        "172.16.0.1",
        "192.168.1.1",
        "127.0.0.1",
        "169.254.1.1",
        "169.254.169.254",
        "0.0.0.0",
        "224.0.0.1",
        "::1",
        "fc00::1",
        "fe80::1",
    ],
)
async def test_private_reserved_and_metadata_addresses_are_rejected(
    address: str,
) -> None:
    with pytest.raises(SecurityBoundaryError):
        await validate_target("https://example.com", resolver_for(address))


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "data:text/html,hello",
        "ftp://example.com/file",
        "https://user:pass@example.com",
        "https://example.com:8443",
    ],
)
async def test_unsafe_navigation_targets_are_rejected(url: str) -> None:
    with pytest.raises(SecurityBoundaryError):
        await validate_target(url, resolver_for(PUBLIC_IP))


async def test_public_https_target_is_accepted() -> None:
    target = await validate_target(
        "https://example.com/path",
        resolver_for(PUBLIC_IP),
    )
    assert target.host == "example.com"
    assert target.port == 443
    assert target.addresses == (PUBLIC_IP,)


async def test_dns_rebind_is_caught_by_connection_time_revalidation() -> None:
    answers = iter(((PUBLIC_IP,), ("127.0.0.1",)))

    async def flipping_resolver(_host: str, _port: int) -> Sequence[str]:
        return next(answers)

    url = "https://example.com"
    await validate_target(url, flipping_resolver)
    guard = NetworkGuard(SecurityPolicy(), resolver=flipping_resolver)
    with pytest.raises(SecurityBoundaryError):
        await guard.validate_request(url)


async def test_redirect_destination_is_revalidated() -> None:
    guard = NetworkGuard(
        SecurityPolicy(),
        resolver=resolver_for("192.168.1.50"),
    )
    with pytest.raises(SecurityBoundaryError):
        await guard.validate_request("http://internal.example/redirected")


async def test_request_count_cap() -> None:
    guard = NetworkGuard(
        SecurityPolicy(max_requests=2),
        resolver=resolver_for(PUBLIC_IP),
    )
    await guard.validate_request("https://example.com/one")
    await guard.validate_request("https://example.com/two")
    with pytest.raises(RequestLimitError):
        await guard.validate_request("https://example.com/three")


def test_aggregate_response_byte_cap() -> None:
    guard = NetworkGuard(SecurityPolicy(max_response_bytes=10))
    guard.add_response_bytes(6)
    with pytest.raises(ResponseBytesLimitError):
        guard.add_response_bytes(5)


async def test_hard_timeout_cancels_work() -> None:
    cancelled = asyncio.Event()

    async def work() -> None:
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.set()

    with pytest.raises(RenderTimeoutError):
        await run_with_hard_timeout(work(), timeout_ms=10)
    assert cancelled.is_set()
