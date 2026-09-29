import logging
import threading
import time
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
from app.api.data import router as data_router
from app.api.auth import router as auth_router
from app.auth import verify_token
from app.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)
_rate_limit_lock = threading.Lock()
_rate_limit_buckets: dict[str, list[float]] = {}


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
        allow_headers=["Content-Type", "Authorization"],
    )

    public_paths = {"/health", "/health/ready", "/docs", "/openapi.json", "/redoc", "/api/v1/system/capabilities"}

    @app.middleware("http")
    async def security_boundary(request: Request, call_next):
        now = time.monotonic()
        client_ip = request.client.host if request.client else "unknown"
        should_limit = settings.rate_limit_enabled and request.url.path not in public_paths and request.method != "OPTIONS"
        if should_limit:
            bucket_key = f"{client_ip}:{request.url.path.split('/')[1]}"
            with _rate_limit_lock:
                bucket = [item for item in _rate_limit_buckets.get(bucket_key, []) if now - item < settings.rate_limit_window_seconds]
                if len(bucket) >= settings.rate_limit_requests:
                    response = JSONResponse(status_code=429, content={"detail": "请求过于频繁，请稍后重试"})
                    response.headers["Retry-After"] = str(settings.rate_limit_window_seconds)
                    return response
                bucket.append(now)
                _rate_limit_buckets[bucket_key] = bucket

        is_public = request.url.path in public_paths or request.url.path.startswith("/api/auth") or request.method == "OPTIONS"
        if settings.auth_enabled and request.url.path.startswith("/api") and not is_public:
            authorization = request.headers.get("Authorization", "")
            if not authorization.lower().startswith("bearer ") or verify_token(authorization[7:].strip()) is None:
                return JSONResponse(status_code=401, content={"detail": "需要登录后访问"})

        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        return response

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
    app.include_router(data_router)
    app.include_router(auth_router)
    return app


app = create_app()
