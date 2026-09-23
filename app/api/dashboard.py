"""闭环工作台聚合数据接口。"""

from fastapi import APIRouter

from app.database import get_dashboard_summary
from app.services.next_actions_service import get_next_actions
from app.schemas import DashboardSummary, NextActionResponse


router = APIRouter(prefix="/api/dashboard", tags=["Dashboard"])


@router.get("/summary", response_model=DashboardSummary)
def get_summary() -> DashboardSummary:
    return DashboardSummary.model_validate(get_dashboard_summary())


@router.get('/next-actions', response_model=list[NextActionResponse])
def get_next_actions_for_workspace() -> list[NextActionResponse]:
    return [NextActionResponse.model_validate(item) for item in get_next_actions()]
