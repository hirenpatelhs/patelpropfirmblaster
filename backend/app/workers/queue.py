import json
import os
from collections.abc import Awaitable, Callable
from uuid import uuid4

import structlog
from redis.asyncio import Redis
from redis.exceptions import ResponseError

from app.core.config import settings


logger = structlog.get_logger()
Handler = Callable[[dict[str, object]], Awaitable[None]]


class WorkQueue:
    """Crash-recoverable Redis Stream queue with explicit acknowledgement."""

    def __init__(self, name: str = "ppb:jobs:stream", group: str = "ppb:workers", claim_idle_ms: int | None = None) -> None:
        self.redis = Redis.from_url(settings.redis_url, decode_responses=True)
        self.name = name
        self.group = group
        self.consumer = f"worker-{os.getpid()}-{uuid4().hex[:8]}"
        self.claim_idle_ms = claim_idle_ms or settings.worker_claim_idle_ms

    async def enqueue(self, kind: str, payload: dict[str, object]) -> None:
        enriched = dict(payload)
        enriched.setdefault("correlation_id", str(uuid4()))
        await self.redis.xadd(self.name, {"job": json.dumps({"kind": kind, "payload": enriched}, default=str)})

    async def ensure_group(self) -> None:
        try:
            await self.redis.xgroup_create(self.name, self.group, id="0", mkstream=True)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def run(self, handlers: dict[str, Handler]) -> None:
        await self.ensure_group()
        while True:
            claimed = await self._claim_stale()
            messages = claimed or await self.redis.xreadgroup(self.group, self.consumer, {self.name: ">"}, count=1, block=10_000)
            if not messages:
                continue
            for _, entries in messages:
                for message_id, fields in entries:
                    await self._process(message_id, fields, handlers)

    async def _claim_stale(self) -> list[tuple[str, list[tuple[str, dict[str, str]]]]]:
        claimed = await self.redis.xautoclaim(self.name, self.group, self.consumer, min_idle_time=self.claim_idle_ms, start_id="0-0", count=10)
        entries = claimed[1] if len(claimed) > 1 else []
        return [(self.name, entries)] if entries else []

    async def _process(self, message_id: str, fields: dict[str, str], handlers: dict[str, Handler]) -> None:
        correlation_id = "unparsed"
        try:
            job = json.loads(fields["job"])
            correlation_id = str(job["payload"].get("correlation_id", "missing"))
            handler = handlers.get(job["kind"])
            if handler is None:
                logger.error("unknown_job", kind=job["kind"], message_id=message_id, correlation_id=correlation_id)
            else:
                await handler(job["payload"])
        except Exception:
            # No acknowledgement: another worker can claim this pending event
            # after the idle threshold. Durable application idempotency handles
            # the at-least-once delivery contract.
            logger.exception("job_failed", message_id=message_id, correlation_id=correlation_id)
            return
        await self.redis.xack(self.name, self.group, message_id)
        await self.redis.xdel(self.name, message_id)
