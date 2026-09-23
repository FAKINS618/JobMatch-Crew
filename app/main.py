import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.health import router as health_router
from app.api.job_match import router as job_match_router
from app.api.job_search import router as job_search_router
from app.api.reports import router as reports_router
from app.api.roles import router as roles_router
from app.api.resumes import router as resumes_router
from app.database import init_db, recover_stale_background_tasks
from app.api.analysis_tasks import router as analysis_tasks_router
from app.api.action_items import router as action_items_router
from app.api.dashboard import router as dashboard_router
from app.api.job_targets import router as job_targets_router
from app.api.copilot import router as copilot_router
from app.api.system import router as system_router
from app.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """管理 FastAPI 应用生命周期。

    启动阶段初始化本地数据库；
    后续如果接入向量库、任务队列、连接池，也可以统一放在这里。
    """
    init_db()
    recovered = recover_stale_background_tasks(settings.task_stale_after_seconds)
    if any(recovered.values()):
        logger.warning(
            "Marked stale background work as failed: %s", recovered
        )
    yield


def create_app() -> FastAPI:
    """创建 FastAPI 应用并挂载所有业务路由。"""
    app = FastAPI(
        title="JobMatch Crew API",
        version="0.5.0",
        lifespan=lifespan,
    )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        """Log unexpected failures without exposing internal details to clients."""
        logger.exception(
            "Unhandled application error method=%s path=%s",
            request.method,
            request.url.path,
        )
        return JSONResponse(
            status_code=500,
            content={"detail": "服务暂时不可用，请稍后重试"},
        )

    allowed_origins = [
        origin.strip()
        for origin in settings.cors_allowed_origins.split(",")
        if origin.strip()
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
        allow_headers=["Content-Type"],
    )

    app.include_router(health_router)
    app.include_router(job_match_router)
    app.include_router(job_search_router)
    app.include_router(reports_router)
    app.include_router(resumes_router)
    app.include_router(roles_router)
    app.include_router(analysis_tasks_router)
    app.include_router(action_items_router)
    app.include_router(dashboard_router)
    app.include_router(job_targets_router)
    app.include_router(copilot_router)
    app.include_router(system_router)
    return app


app = create_app()
