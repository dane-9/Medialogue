"""Small bounded in-process login failure limiter.

Medialogue is a single-process appliance, so an in-memory limiter is enough
for the current deployment model. It deliberately tracks failures rather than
locking accounts permanently: a local operator cannot create an account-lock
out denial of service for the administrator.
"""

from __future__ import annotations

import time
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass, field


LOGIN_FAILURE_LIMIT = 5
LOGIN_FAILURE_WINDOW_SECONDS = 5 * 60
LOGIN_RATE_LIMIT_MAX_KEYS = 4096


@dataclass(slots=True)
class _FailureBucket:
    failures: deque[float] = field(default_factory=deque)
    last_seen: float = 0.0


class LoginRateLimiter:
    def __init__(
        self,
        *,
        failure_limit: int = LOGIN_FAILURE_LIMIT,
        window_seconds: float = LOGIN_FAILURE_WINDOW_SECONDS,
        max_keys: int = LOGIN_RATE_LIMIT_MAX_KEYS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.failure_limit = max(1, int(failure_limit))
        self.window_seconds = max(1.0, float(window_seconds))
        self.max_keys = max(1, int(max_keys))
        self.clock = clock
        self._buckets: OrderedDict[str, _FailureBucket] = OrderedDict()

    def _prune(self, now: float) -> None:
        cutoff = now - self.window_seconds
        stale_keys: list[str] = []
        for key, bucket in self._buckets.items():
            while bucket.failures and bucket.failures[0] <= cutoff:
                bucket.failures.popleft()
            if not bucket.failures:
                stale_keys.append(key)
        for key in stale_keys:
            self._buckets.pop(key, None)
        while len(self._buckets) > self.max_keys:
            self._buckets.popitem(last=False)

    def retry_after(self, key: str) -> int | None:
        now = self.clock()
        self._prune(now)
        bucket = self._buckets.get(key)
        if bucket is None or len(bucket.failures) < self.failure_limit:
            return None
        bucket.last_seen = now
        self._buckets.move_to_end(key)
        return max(1, int(bucket.failures[0] + self.window_seconds - now + 0.999))

    def record_failure(self, key: str) -> None:
        now = self.clock()
        self._prune(now)
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = _FailureBucket()
            self._buckets[key] = bucket
        bucket.failures.append(now)
        bucket.last_seen = now
        self._buckets.move_to_end(key)
        self._prune(now)

    def clear(self, key: str) -> None:
        self._buckets.pop(key, None)

    def reset(self) -> None:
        self._buckets.clear()

    @property
    def key_count(self) -> int:
        self._prune(self.clock())
        return len(self._buckets)


login_rate_limiter = LoginRateLimiter()


def login_attempt_key(username: str, client_host: str | None) -> str:
    normalized_username = username.strip().casefold()
    normalized_host = (client_host or "unknown").strip().casefold() or "unknown"
    return f"{normalized_username}\0{normalized_host}"


__all__ = [
    "LOGIN_FAILURE_LIMIT",
    "LOGIN_FAILURE_WINDOW_SECONDS",
    "LOGIN_RATE_LIMIT_MAX_KEYS",
    "LoginRateLimiter",
    "login_attempt_key",
    "login_rate_limiter",
]
