"""A deliberately dumb rate limiter.

One process, one thread, one scheduled run - a monotonic clock and a sleep is
all this needs. The point is that the ceiling is configurable and always
enforced, not that it is clever.
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)


class RateLimiter:
    def __init__(self, requests_per_minute: float) -> None:
        if requests_per_minute <= 0:
            raise ValueError("requests_per_minute must be > 0")
        self.min_interval = 60.0 / requests_per_minute
        self._last_call: float | None = None

    def wait(self) -> None:
        now = time.monotonic()
        if self._last_call is not None:
            elapsed = now - self._last_call
            remaining = self.min_interval - elapsed
            if remaining > 0:
                logger.debug("rate limit sleep", extra={"seconds": round(remaining, 2)})
                time.sleep(remaining)
        self._last_call = time.monotonic()
