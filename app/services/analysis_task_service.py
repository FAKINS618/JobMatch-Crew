import logging

from app.database import update_analysis_task, update_resume_market_search_trigger
from app.schemas import MarketMatchRequest
from app.services.market_match_service import generate_market_match_report


logger = logging.getLogger(__name__)


def run_market_match_task(task_id: int, payload: MarketMatchRequest) -> None:
    """后台执行岗位市场匹配分析。"""
    update_analysis_task(task_id, status="running", progress=10)

    try:
        result = generate_market_match_report(payload)
    except Exception as exc:
        logger.exception("Market match async task failed")
        update_analysis_task(
            task_id,
            status="failed",
            progress=100,
            error_message=str(exc),
        )
        return

    update_analysis_task(
        task_id,
        status="success",
        progress=100,
        report_id=result.report_id,
    )


def run_auto_market_match_task(
    task_id: int, payload: MarketMatchRequest, trigger_id: int
) -> None:
    """Run the existing market task while keeping the opt-in trigger auditable."""
    update_resume_market_search_trigger(trigger_id, status="running")
    update_analysis_task(task_id, status="running", progress=10)
    try:
        result = generate_market_match_report(payload)
    except Exception as exc:
        logger.exception("Automatic market match task failed")
        update_analysis_task(task_id, status="failed", progress=100, error_message=str(exc))
        update_resume_market_search_trigger(
            trigger_id, status="failed", reason="岗位搜索失败，请在岗位收件箱中重试。"
        )
        return
    update_analysis_task(task_id, status="success", progress=100, report_id=result.report_id)
    update_resume_market_search_trigger(
        trigger_id, status="success", report_id=result.report_id
    )
