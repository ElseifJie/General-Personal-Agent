"""Rate limiter with a Redis sliding window and an in-process fallback.

Redis is the production backend (shared by all workers); memory remains for
local development and as a degraded fallback when Redis is unreachable.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import defaultdict, deque

from general_personal_agent.gpa.logging import get_logger

logger = get_logger(__name__)

_LUA = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local member = ARGV[4]
redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
local count = redis.call('ZCARD', key)
if count >= limit then
  return 0
end
redis.call('ZADD', key, now, member)
redis.call('PEXPIRE', key, window)
return 1
"""


class GpaRateLimiter:
    def __init__(self, redis_url: str = "", prefix: str = "gpa:rl") -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()
        self._prefix = prefix
        self._redis = None
        if redis_url:
            try:
                from redis.asyncio import Redis

                self._redis = Redis.from_url(redis_url, decode_responses=True)
            except Exception as exc:  # noqa: BLE001
                logger.warning("redis rate limiter unavailable, using memory: %s", exc)

    async def allow(self, key: str, limit: int, window_s: float = 60.0) -> bool:
        if limit <= 0:
            return True
        if self._redis is not None:
            try:
                now_ms = int(time.time() * 1000)
                allowed = await self._redis.eval(
                    _LUA,
                    1,
                    f"{self._prefix}:{key}",
                    now_ms,
                    int(window_s * 1000),
                    limit,
                    f"{now_ms}:{uuid.uuid4().hex}",
                )
                return bool(int(allowed))
            except Exception as exc:  # noqa: BLE001 - availability fallback
                logger.warning("redis rate limit failed, falling back to memory: %s", exc)
        return await self._allow_memory(key, limit, window_s)

    async def _allow_memory(self, key: str, limit: int, window_s: float) -> bool:
        now = time.monotonic()
        async with self._lock:
            q = self._hits[key]
            while q and now - q[0] >= window_s:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            return True

    async def reset(self) -> None:
        async with self._lock:
            self._hits.clear()

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None
