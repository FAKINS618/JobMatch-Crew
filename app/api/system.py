"""Non-sensitive local runtime capability information."""

from fastapi import APIRouter

from app.agent_pipeline.retrieval import build_resume_retriever
from app.config import settings


router = APIRouter(prefix="/api/v1/system", tags=["System"])


@router.get("/capabilities")
def capabilities() -> dict[str, object]:
    retriever = build_resume_retriever(settings)
    return {
        "app_version": "0.5.0",
        "storage_mode": "local_sqlite",
        "llm_configured": bool(settings.deepseek_api_key),
        "tavily_configured": bool(settings.tavily_api_key),
        "embedding_enabled": bool(settings.embedding_enabled),
        "retrieval_default_strategy": getattr(retriever, "last_strategy", "tfidf"),
        "evidence_feedback_enabled": True,
        "auth_enabled": bool(settings.auth_enabled),
        "rate_limit_enabled": bool(settings.rate_limit_enabled),
    }
