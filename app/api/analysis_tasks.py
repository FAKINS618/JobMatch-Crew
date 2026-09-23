from fastapi import APIRouter, BackgroundTasks, HTTPException

from app.database import create_analysis_task, get_analysis_task, update_analysis_task
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
    task_id = create_analysis_task(task_type="market_match")
    try:
        dispatch_task(
            background_tasks,
            task_type="market_match",
            payload={"task_id": task_id, "request": payload.model_dump(mode="json")},
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

    return {"task_id": task_id, "status": "pending"}


@router.get("/{task_id}")
def get_task_detail(task_id: int) -> dict:
    """查询异步任务状态。"""
    task = get_analysis_task(task_id)

    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")

    return task
