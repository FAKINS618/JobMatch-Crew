"""Optional Redis-backed queue for long-running analysis work."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from typing import Any

from fastapi import BackgroundTasks
from redis import Redis
from redis.exceptions import ResponseError

from app.config import settings


class TaskQueueUnavailable(RuntimeError):
    """Raised when the configured Redis task queue cannot accept work."""


class RedisTaskQueue:
    def __init__(self) -> None:
        self.client = Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=settings.redis_socket_connect_timeout_seconds,
            socket_timeout=settings.redis_socket_timeout_seconds,
        )

    def enqueue(self, task_type: str, payload: dict[str, Any]) -> None:
        envelope = json.dumps(
            {"task_type": task_type, "payload": payload},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        try:
            self.client.xadd(
                settings.task_queue_name,
                {"payload": envelope},
                maxlen=10_000,
                approximate=True,
            )
        except Exception as exc:
            raise TaskQueueUnavailable("任务队列暂不可用") from exc

    def ensure_group(self) -> None:
        try:
            self.client.xgroup_create(
                settings.task_queue_name,
                settings.task_queue_group,
                id="0-0",
                mkstream=True,
            )
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    def consume(
        self,
        consumer_name: str,
        timeout_seconds: int = 5,
    ) -> tuple[str, dict[str, Any]] | None:
        messages = self.client.xreadgroup(
            settings.task_queue_group,
            consumer_name,
            {settings.task_queue_name: ">"},
            count=1,
            block=timeout_seconds * 1000,
        )
        if not messages:
            return None
        _, entries = messages[0]
        message_id, fields = entries[0]
        raw_payload = fields.get("payload")
        payload = json.loads(raw_payload or "")
        if not isinstance(payload, dict):
            raise ValueError("任务队列消息格式无效")
        return message_id, payload

    def reclaim_pending(
        self,
        consumer_name: str,
        count: int = 10,
    ) -> list[tuple[str, dict[str, Any]]]:
        result = self.client.xautoclaim(
            settings.task_queue_name,
            settings.task_queue_group,
            consumer_name,
            min_idle_time=settings.task_queue_visibility_timeout_seconds * 1000,
            start_id="0-0",
            count=count,
        )
        entries = result[1] if len(result) > 1 else []
        reclaimed: list[tuple[str, dict[str, Any]]] = []
        for message_id, fields in entries:
            raw_payload = fields.get("payload")
            payload = json.loads(raw_payload or "")
            if isinstance(payload, dict):
                reclaimed.append((message_id, payload))
        return reclaimed

    def acknowledge(self, message_id: str) -> None:
        self.client.xack(settings.task_queue_name, settings.task_queue_group, message_id)


def new_consumer_name() -> str:
    return f"worker-{uuid.uuid4().hex}"


def dispatch_task(
    background_tasks: BackgroundTasks,
    *,
    task_type: str,
    payload: dict[str, Any],
    local_runner: Callable[..., Any],
    local_args: tuple[Any, ...],
) -> None:
    """Queue work when enabled, otherwise preserve local BackgroundTasks."""
    if settings.task_queue_enabled:
        RedisTaskQueue().enqueue(task_type, payload)
        return
    background_tasks.add_task(local_runner, *local_args)
