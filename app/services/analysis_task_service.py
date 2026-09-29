import logging

from app.database import is_analysis_task_cancelled, update_analysis_task, update_resume_market_search_trigger
from app.schemas import MarketMatchRequest
from app.services.market_match_service import generate_market_match_report


logger = logging.getLogger(__name__)


def run_market_match_task(task_id: int, payload: MarketMatchRequest) -> None:
    """Run a market analysis and never overwrite a user cancellation."""
    update_analysis_task(task_id, status="running", progress=10)
    if is_analysis_task_cancelled(task_id):
        return
    try:
        result = generate_market_match_report(payload)
    except Exception:
        logger.exception("Market match async task failed")
        if not is_analysis_task_cancelled(task_id):
            update_analysis_task(task_id, status="failed", progress=100, error_message="岗位市场分析失败，请稍后重试。")
        return
    if not is_analysis_task_cancelled(task_id):
        update_analysis_task(task_id, status="success", progress=100, report_id=result.report_id)


def run_auto_market_match_task(
    task_id: int, payload: MarketMatchRequest, trigger_id: int
) -> None:
    """Run an automatic market analysis while preserving trigger state."""
    update_resume_market_search_trigger(trigger_id, status="running")
    update_analysis_task(task_id, status="running", progress=10)
    if is_analysis_task_cancelled(task_id):
        update_resume_market_search_trigger(trigger_id, status="skipped", reason="任务已取消")
        return
    try:
        result = generate_market_match_report(payload)
    except Exception:
        logger.exception("Automatic market match task failed")
        if not is_analysis_task_cancelled(task_id):
            update_analysis_task(task_id, status="failed", progress=100, error_message="岗位市场分析失败，请稍后重试。")
            update_resume_market_search_trigger(trigger_id, status="failed", reason="岗位搜索失败，请在岗位收件箱中重试。")
        return
    if is_analysis_task_cancelled(task_id):
        update_resume_market_search_trigger(trigger_id, status="skipped", reason="任务已取消")
        return
    update_analysis_task(task_id, status="success", progress=100, report_id=result.report_id)
    update_resume_market_search_trigger(
        trigger_id, status="success", report_id=result.report_id
    )
