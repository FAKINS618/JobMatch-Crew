"""Run Redis-backed analysis tasks in a separate process.

Start with: ``python -m app.task_worker``
"""

from __future__ import annotations

import logging

from app.schemas import MarketMatchRequest
from app.services.analysis_task_service import (
    run_auto_market_match_task,
    run_market_match_task,
)
from app.services.copilot_service import run_copilot_turn
from app.task_queue import RedisTaskQueue, new_consumer_name


logger = logging.getLogger(__name__)


def handle_task(message: dict) -> None:
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
                handle_task(payload)
            except Exception:
                logger.exception("Queued task failed: %s", payload.get("task_type"))
                continue
            queue.acknowledge(message_id)


if __name__ == "__main__":
    main()
