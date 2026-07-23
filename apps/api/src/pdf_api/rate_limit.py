"""Small in-process sliding-window limiter for the single API process."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    retry_after: int


class SlidingWindowRateLimiter:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def check(
        self,
        key: str,
        *,
        limit: int,
        window_seconds: int = 60,
    ) -> RateLimitDecision:
        now = self._clock()
        cutoff = now - window_seconds
        async with self._lock:
            events = self._events[key]
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= limit:
                retry_after = max(1, int(events[0] + window_seconds - now) + 1)
                return RateLimitDecision(allowed=False, retry_after=retry_after)
            events.append(now)
            return RateLimitDecision(allowed=True, retry_after=0)

    async def clear(self) -> None:
        async with self._lock:
            self._events.clear()
