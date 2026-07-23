"""Browser network security boundary for untrusted render inputs."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from playwright.async_api import Browser, BrowserContext, Dialog, Frame, Page, Route

MB = 1024 * 1024
METADATA_IPS = frozenset({"169.254.169.254"})
Resolver = Callable[[str, int], Awaitable[Sequence[str]]]


class SecurityBoundaryError(ValueError):
    """An untrusted URL or browser action violated the render policy."""


class RequestLimitError(SecurityBoundaryError):
    """The render exceeded its network-request limit."""


class ResponseBytesLimitError(SecurityBoundaryError):
    """The render exceeded its aggregate response-byte limit."""


class RenderTimeoutError(SecurityBoundaryError):
    """The complete render exceeded its hard time limit."""


@dataclass(frozen=True)
class ValidatedTarget:
    url: str
    host: str
    port: int
    addresses: tuple[str, ...]


@dataclass(frozen=True)
class SecurityPolicy:
    max_requests: int = 150
    max_response_bytes: int = 30 * MB
    request_timeout_ms: int = 15_000


async def resolve_host(host: str, port: int) -> tuple[str, ...]:
    """Resolve all A/AAAA addresses without blocking the event loop."""

    def _resolve() -> tuple[str, ...]:
        rows = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        return tuple(sorted({str(row[4][0]) for row in rows}))

    try:
        return await asyncio.to_thread(_resolve)
    except socket.gaierror as exc:
        raise SecurityBoundaryError(f"target DNS resolution failed: {host}") from exc


def _target_parts(url: str) -> tuple[str, int]:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise SecurityBoundaryError("target URL is malformed") from exc

    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        raise SecurityBoundaryError("target scheme must be http or https")
    if parsed.username is not None or parsed.password is not None:
        raise SecurityBoundaryError("target URL credentials are not allowed")
    if not parsed.hostname:
        raise SecurityBoundaryError("target URL must contain a hostname")

    resolved_port = port or (443 if scheme == "https" else 80)
    if resolved_port not in {80, 443}:
        raise SecurityBoundaryError("target port must be 80 or 443")
    return parsed.hostname, resolved_port


def _forbidden_address(raw: str) -> bool:
    if "%" in raw:
        return True
    try:
        address = ipaddress.ip_address(raw)
    except ValueError:
        return True
    return str(address) in METADATA_IPS or not address.is_global or address.is_multicast


async def validate_target(url: str, resolver: Resolver = resolve_host) -> ValidatedTarget:
    """Resolve and reject targets that are not globally routable HTTP(S)."""
    host, port = _target_parts(url)
    addresses = tuple(await resolver(host, port))
    if not addresses:
        raise SecurityBoundaryError("target DNS resolution returned no addresses")
    forbidden = tuple(address for address in addresses if _forbidden_address(address))
    if forbidden:
        raise SecurityBoundaryError(f"target resolved to a forbidden address: {', '.join(forbidden)}")
    return ValidatedTarget(url=url, host=host, port=port, addresses=addresses)


async def run_with_hard_timeout[T](awaitable: Awaitable[T], timeout_ms: int) -> T:
    """Cancel an operation when the complete render deadline expires."""
    try:
        async with asyncio.timeout(timeout_ms / 1000):
            return await awaitable
    except TimeoutError as exc:
        raise RenderTimeoutError(f"render exceeded {timeout_ms} ms") from exc


@dataclass
class NetworkGuard:
    """Per-render request and byte accounting plus revalidation."""

    policy: SecurityPolicy
    resolver: Resolver = resolve_host
    requests_seen: int = 0
    response_bytes: int = 0
    violation: SecurityBoundaryError | None = None
    _tasks: set[asyncio.Task[Any]] = field(default_factory=set, init=False)

    def _remember(self, error: SecurityBoundaryError) -> SecurityBoundaryError:
        if self.violation is None:
            self.violation = error
        return self.violation

    def raise_if_violated(self) -> None:
        if self.violation is not None:
            raise self.violation

    async def validate_request(self, url: str) -> ValidatedTarget:
        self.raise_if_violated()
        self.requests_seen += 1
        if self.requests_seen > self.policy.max_requests:
            raise self._remember(RequestLimitError(f"network request limit exceeded: {self.policy.max_requests}"))
        try:
            return await validate_target(url, self.resolver)
        except SecurityBoundaryError as exc:
            raise self._remember(exc) from exc

    def add_response_bytes(self, size: int) -> None:
        self.raise_if_violated()
        self.response_bytes += size
        if self.response_bytes > self.policy.max_response_bytes:
            raise self._remember(
                ResponseBytesLimitError(f"aggregate response byte limit exceeded: {self.policy.max_response_bytes}")
            )

    async def handle_route(self, route: Route) -> None:
        """Re-resolve immediately before each connection, including redirects."""
        try:
            await self.validate_request(route.request.url)
            response = await route.fetch(
                max_redirects=0,
                timeout=self.policy.request_timeout_ms,
            )
            body = await response.body()
            self.add_response_bytes(len(body))
            await route.fulfill(response=response, body=body)
        except SecurityBoundaryError:
            await route.abort()
        except Exception:
            # Ordinary upstream failures remain navigation failures, not policy
            # violations. Aborting makes Playwright fail the owning operation.
            await route.abort()

    def _track(self, awaitable: Coroutine[Any, Any, Any]) -> None:
        task = asyncio.create_task(awaitable)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def attach_page(self, page: Page) -> None:
        page.on("popup", self._block_popup)
        page.on("dialog", self._dismiss_dialog)
        page.on("framenavigated", self._check_frame)

    def _block_popup(self, popup: Page) -> None:
        self._track(popup.close())

    def _dismiss_dialog(self, dialog: Dialog) -> None:
        self._track(dialog.dismiss())

    def _check_frame(self, frame: Frame) -> None:
        scheme = urlsplit(frame.url).scheme.lower()
        if scheme in {"file", "data"}:
            self._remember(SecurityBoundaryError(f"{scheme}: frame navigation is blocked"))
            self._track(frame.goto("about:blank"))

    async def drain_tasks(self) -> None:
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)


@dataclass
class GuardedBrowserContext:
    context: BrowserContext
    page: Page
    guard: NetworkGuard

    def raise_if_violated(self) -> None:
        self.guard.raise_if_violated()


@asynccontextmanager
async def guarded_context(
    browser: Browser,
    *,
    policy: SecurityPolicy,
    resolver: Resolver = resolve_host,
) -> AsyncIterator[GuardedBrowserContext]:
    """Create one isolated, guarded browser context for one render job."""
    context = await browser.new_context(
        accept_downloads=False,
        service_workers="block",
    )
    guard = NetworkGuard(policy=policy, resolver=resolver)
    try:
        await context.route("**/*", guard.handle_route)
        await context.add_init_script("window.open = () => null;")
        page = await context.new_page()
        guard.attach_page(page)
        yield GuardedBrowserContext(context=context, page=page, guard=guard)
    finally:
        await guard.drain_tasks()
        await context.close()
