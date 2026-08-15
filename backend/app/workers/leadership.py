from uuid import uuid4

from redis.asyncio import Redis


RENEW_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('expire', KEYS[1], ARGV[2])
end
return 0
"""

RELEASE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
end
return 0
"""


class SchedulerLeadership:
    """Ownership-safe Redis lease; only its token can renew or release it."""

    def __init__(self, redis: Redis, key: str = "ppb:lock:scheduler", ttl_seconds: int = 10) -> None:
        if ttl_seconds < 3:
            raise ValueError("Scheduler lease TTL must be at least three seconds")
        self.redis = redis
        self.key = key
        self.ttl_seconds = ttl_seconds
        self.token = str(uuid4())
        self.held = False

    async def acquire(self) -> bool:
        self.held = bool(await self.redis.set(self.key, self.token, nx=True, ex=self.ttl_seconds))
        return self.held

    async def renew(self) -> bool:
        if not self.held:
            return False
        renewed = bool(await self.redis.eval(RENEW_SCRIPT, 1, self.key, self.token, self.ttl_seconds))
        self.held = renewed
        return renewed

    async def release(self) -> bool:
        if not self.held:
            return False
        released = bool(await self.redis.eval(RELEASE_SCRIPT, 1, self.key, self.token))
        self.held = False
        return released
