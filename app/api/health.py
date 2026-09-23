import logging

from fastapi import APIRouter, HTTPException

from app import database


router = APIRouter(tags=["Health"])
logger = logging.getLogger(__name__)


@router.get("/health")
def health() -> dict[str, str]:
    """服务健康检查，用于确认后端是否启动成功。"""
    return {"status": "ok"}


@router.get("/health/ready")
def readiness() -> dict[str, object]:
    """Confirm the local SQLite schema is ready without exposing internals."""
    required_tables = {
        "analysis_turns",
        "analysis_runs",
        "requirement_evidence",
        "evidence_feedback",
    }
    try:
        with database.connect_db() as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        available_tables = {row[0] for row in rows}
        if not required_tables.issubset(available_tables):
            raise RuntimeError("required schema is incomplete")
    except Exception as error:
        # Keep the diagnostic private; callers only need the readiness state.
        logger.exception("Readiness check failed")
        raise HTTPException(status_code=503, detail="数据库尚未就绪") from error
    return {"status": "ready", "database_ready": True}
