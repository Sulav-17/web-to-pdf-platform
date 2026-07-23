"""Chromium PDF renderer with a guarded, recycling browser pool."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from playwright.async_api import Browser, Playwright, async_playwright

from .logging_config import get_logger
from .schemas import RenderOptions
from .security import (
    Resolver,
    SecurityPolicy,
    guarded_context,
    resolve_host,
    run_with_hard_timeout,
    validate_target,
)

log = get_logger(__name__)


class RenderError(Exception):
    """A render failed (navigation, timeout, crash, or browser error)."""


class OutputTooLargeError(RenderError):
    """The produced PDF exceeded the configured maximum size."""


@dataclass
class _Handle:
    browser: Browser
    inflight: int = 0
    retiring: bool = False


@dataclass
class RenderResult:
    pdf: bytes
    duration_ms: int


@dataclass
class BrowserPool:
    max_concurrent: int = 3
    recycle_after: int = 50
    max_output_bytes: int = 50 * 1024 * 1024
    max_network_requests: int = 150
    max_network_bytes: int = 30 * 1024 * 1024
    resolver: Resolver = field(default=resolve_host, repr=False)

    _pw: Playwright | None = field(default=None, init=False)
    _current: _Handle | None = field(default=None, init=False)
    _jobs_since_launch: int = field(default=0, init=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    _sem: asyncio.Semaphore | None = field(default=None, init=False)

    async def start(self) -> None:
        self._pw = await async_playwright().start()
        self._sem = asyncio.Semaphore(self.max_concurrent)
        self._current = _Handle(browser=await self._launch())
        self._jobs_since_launch = 0
        log.info("renderer.started", max_concurrent=self.max_concurrent)

    async def _launch(self) -> Browser:
        assert self._pw is not None
        return await self._pw.chromium.launch(
            headless=True,
            args=["--disable-dev-shm-usage"],
        )

    async def stop(self) -> None:
        async with self._lock:
            if self._current is not None:
                try:
                    await self._current.browser.close()
                except Exception as exc:  # pragma: no cover - best effort
                    log.warning("renderer.close_failed", error=str(exc))
                self._current = None
        if self._pw is not None:
            await self._pw.stop()
            self._pw = None
        log.info("renderer.stopped")

    async def _recycle_locked(self) -> None:
        new = _Handle(browser=await self._launch())
        old = self._current
        if old is not None:
            old.retiring = True
        self._current = new
        self._jobs_since_launch = 0
        log.info("renderer.recycled")
        if old is not None and old.inflight == 0:
            await old.browser.close()

    async def _acquire(self) -> _Handle:
        async with self._lock:
            assert self._current is not None
            if not self._current.browser.is_connected():
                old = self._current
                old.retiring = True
                self._current = _Handle(browser=await self._launch())
                self._jobs_since_launch = 0
                if old.inflight == 0:
                    await old.browser.close()
                log.warning("renderer.reconnect", reason="browser disconnected")
            elif self._jobs_since_launch >= self.recycle_after:
                await self._recycle_locked()
            handle = self._current
            handle.inflight += 1
            self._jobs_since_launch += 1
            return handle

    async def _release(self, handle: _Handle) -> None:
        async with self._lock:
            handle.inflight -= 1
            if handle.retiring and handle.inflight == 0:
                try:
                    await handle.browser.close()
                except Exception as exc:  # pragma: no cover
                    log.warning("renderer.close_failed", error=str(exc))

    async def render(self, *, kind: str, source: str, options: RenderOptions) -> RenderResult:
        assert self._sem is not None, "pool not started"
        started = time.monotonic()
        async with self._sem:
            handle = await self._acquire()
            try:
                pdf = await self._render_once(handle.browser, kind, source, options)
            except OutputTooLargeError:
                raise
            except Exception as exc:
                raise RenderError(str(exc)) from exc
            finally:
                await self._release(handle)

        if len(pdf) > self.max_output_bytes:
            raise OutputTooLargeError(f"output {len(pdf)} bytes exceeds limit {self.max_output_bytes}")
        return RenderResult(
            pdf=pdf,
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    async def _render_once(self, browser: Browser, kind: str, source: str, options: RenderOptions) -> bytes:
        async def _render_guarded() -> bytes:
            if kind == "url":
                # Validate once before browser work, then again in the route hook
                # immediately before every connection and redirect.
                await validate_target(source, self.resolver)

            policy = SecurityPolicy(
                max_requests=self.max_network_requests,
                max_response_bytes=self.max_network_bytes,
                request_timeout_ms=options.timeout_ms,
            )
            async with guarded_context(
                browser,
                policy=policy,
                resolver=self.resolver,
            ) as guarded:
                page = guarded.page
                page.set_default_timeout(options.timeout_ms)
                wait_until: Any = options.wait_until
                try:
                    if kind == "url":
                        await page.goto(
                            source,
                            wait_until=wait_until,
                            timeout=options.timeout_ms,
                        )
                    else:
                        await page.set_content(
                            source,
                            wait_until=wait_until,
                            timeout=options.timeout_ms,
                        )
                except Exception:
                    guarded.raise_if_violated()
                    raise
                guarded.raise_if_violated()

                margin = f"{options.margin_mm}mm"
                pdf_kwargs: dict[str, Any] = {
                    "format": options.page_size,
                    "landscape": options.landscape,
                    "scale": options.scale,
                    "print_background": True,
                    "margin": {
                        "top": margin,
                        "bottom": margin,
                        "left": margin,
                        "right": margin,
                    },
                }
                if options.header_template is not None or options.footer_template is not None:
                    pdf_kwargs["display_header_footer"] = True
                    pdf_kwargs["header_template"] = options.header_template or "<span></span>"
                    pdf_kwargs["footer_template"] = options.footer_template or "<span></span>"

                pdf = await page.pdf(**pdf_kwargs)
                guarded.raise_if_violated()
                return pdf

        return await run_with_hard_timeout(_render_guarded(), options.timeout_ms)
