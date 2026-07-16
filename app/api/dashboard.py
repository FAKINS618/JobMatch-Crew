"""闭环工作台聚合数据接口。"""

from fastapi import APIRouter

from app.database import get_dashboard_summary
from app.schemas import DashboardSummary


router = APIRouter(prefix="/api/dashboard", tags=["Dashboard"])


@router.get("/summary", response_model=DashboardSummary)
def get_summary() -> DashboardSummary:
    return DashboardSummary.model_validate(get_dashboard_summary())
