from fastapi import APIRouter, BackgroundTasks, HTTPException

from app.database import (
    cancel_analysis_task,
    create_analysis_task,
    get_analysis_task,
    get_analysis_task_payload,
    retry_analysis_task,
    update_analysis_task,
)
from app.schemas import MarketMatchRequest
from app.services.analysis_task_service import run_market_match_task
from app.task_queue import TaskQueueUnavailable, dispatch_task


router = APIRouter(prefix="/api/tasks",tags=["Analysis Tasks"])
@router.post("/market-match")
def create_market_match_task(
    payload: MarketMatchRequest,
    background_tasks: BackgroundTasks,
) -> dict:
    """创建岗位市场匹配异步任务。"""
    request_payload = payload.model_dump(mode="json")
    task_id = create_analysis_task(task_type="market_match", payload={"request": request_payload})
    try:
        dispatch_task(
            background_tasks,
            task_type="market_match",
            payload={"task_id": task_id, "request": request_payload},
            local_runner=run_market_match_task,
            local_args=(task_id, payload),
        )
    except TaskQueueUnavailable as exc:
        update_analysis_task(
            task_id,
            status="failed",
            progress=100,
            error_message="任务队列暂不可用，请稍后重试。",
        )
        raise HTTPException(status_code=503, detail="任务队列暂不可用，请稍后重试") from exc

    return {"task_id": task_id, "status": "pending", "attempt_count": 0}


@router.get("/{task_id}")
def get_task_detail(task_id: int) -> dict:
    """查询异步任务状态。"""
    task = get_analysis_task(task_id)

    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")

    return task


@router.post("/{task_id}/retry")
def retry_task(task_id: int, background_tasks: BackgroundTasks) -> dict:
    try:
        task = retry_analysis_task(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task["task_type"] != "market_match":
        raise HTTPException(status_code=422, detail="当前任务类型不支持重试")
    payload = get_analysis_task_payload(task_id) or {}
    request = MarketMatchRequest.model_validate(payload.get("request", {}))
    try:
        dispatch_task(
            background_tasks,
            task_type="market_match",
            payload={"task_id": task_id, "request": request.model_dump(mode="json")},
            local_runner=run_market_match_task,
            local_args=(task_id, request),
        )
    except TaskQueueUnavailable as exc:
        update_analysis_task(task_id, status="failed", progress=100, error_message="任务队列暂不可用，请稍后重试。")
        raise HTTPException(status_code=503, detail="任务队列暂不可用，请稍后重试") from exc
    return {"task_id": task_id, "status": "pending", "attempt_count": task["attempt_count"]}


@router.post("/{task_id}/cancel")
def cancel_task(task_id: int) -> dict:
    try:
        task = cancel_analysis_task(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task
