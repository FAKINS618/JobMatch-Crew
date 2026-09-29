"""Run Redis-backed analysis tasks in a separate process.

Start with: ``python -m app.task_worker``
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from app.schemas import MarketMatchRequest
from app.services.analysis_task_service import (
    run_auto_market_match_task,
    run_market_match_task,
)
from app.services.copilot_service import run_copilot_turn
from app.task_queue import RedisTaskQueue, new_consumer_name
from app.database import update_analysis_task
from app.auth import current_user_id
from app.config import settings


logger = logging.getLogger(__name__)


@contextmanager
def _task_user_context(message: dict) -> Iterator[None]:
    user_id = message.get("user_id")
    try:
        parsed_user_id = int(user_id) if user_id is not None else None
    except (TypeError, ValueError) as exc:
        raise ValueError("任务消息的用户身份无效") from exc
    if settings.auth_enabled and (parsed_user_id is None or parsed_user_id <= 0):
        raise ValueError("任务消息缺少有效用户身份")
    token = current_user_id.set(parsed_user_id)
    try:
        yield
    finally:
        current_user_id.reset(token)


def handle_task(message: dict) -> None:
    with _task_user_context(message):
        _handle_task(message)


def _handle_task(message: dict) -> None:
    task_type = message.get("task_type")
    payload = message.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("任务消息缺少 payload")

    if task_type == "market_match":
        run_market_match_task(
            int(payload["task_id"]),
            MarketMatchRequest.model_validate(payload["request"]),
        )
    elif task_type == "auto_market_match":
        run_auto_market_match_task(
            int(payload["task_id"]),
            MarketMatchRequest.model_validate(payload["request"]),
            int(payload["trigger_id"]),
        )
    elif task_type == "copilot_turn":
        run_copilot_turn(int(payload["turn_id"]))
    else:
        raise ValueError(f"未知任务类型: {task_type}")


def main() -> None:
    queue = RedisTaskQueue()
    consumer_name = new_consumer_name()
    queue.ensure_group()
    logger.info("Task worker listening on Redis stream %s", queue.client)
    while True:
        messages = queue.reclaim_pending(consumer_name)
        message = queue.consume(consumer_name)
        if message is not None:
            messages.append(message)
        for message_id, payload in messages:
            try:
                with _task_user_context(payload):
                    _handle_task(payload)
            except Exception:
                logger.exception(
                    "Queued task failed task_type=%s message_id=%s",
                    payload.get("task_type"),
                    message_id,
                )
                try:
                    with _task_user_context(payload):
                        task_id = payload.get("payload", {}).get("task_id") if isinstance(payload.get("payload"), dict) else None
                        if task_id is not None:
                            update_analysis_task(
                                int(task_id),
                                status="failed",
                                progress=100,
                                error_message="任务消息处理失败，请重试。",
                            )
                except Exception:
                    logger.exception("Could not mark failed queued task message_id=%s", message_id)
            queue.acknowledge(message_id)


if __name__ == "__main__":
    main()
