import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.health import router as health_router
from app.api.job_match import router as job_match_router
from app.api.job_search import router as job_search_router
from app.api.reports import router as reports_router
from app.api.roles import router as roles_router
from app.api.market_match import router as market_match_router
from app.api.resumes import router as resumes_router
from app.database import init_db
from app.api.analysis_tasks import router as analysis_tasks_router

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """管理 FastAPI 应用生命周期。

    启动阶段初始化本地数据库；
    后续如果接入向量库、任务队列、连接池，也可以统一放在这里。
    """
    init_db()
    yield


def create_app() -> FastAPI:
    """创建 FastAPI 应用并挂载所有业务路由。"""
    app = FastAPI(
        title="JobMatch Crew API",
        version="0.5.0",
        lifespan=lifespan,
    )

    app.include_router(health_router)
    app.include_router(job_match_router)
    app.include_router(job_search_router)
    app.include_router(reports_router)
    app.include_router(resumes_router)
    app.include_router(roles_router)
    app.include_router(market_match_router)
    app.include_router(analysis_tasks_router)
    return app


app = create_app()
