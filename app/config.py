from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """项目统一配置。

    环境差异统一放到 .env，业务代码只读取 settings，避免到处 os.getenv。
    """

    model: str = "deepseek-v4-pro"
    deepseek_api_key: str | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    temperature: float = 0.2
    max_tokens: int = 8000

    tavily_api_key: str | None = None
    database_path: Path = BASE_DIR / "jobmatch.db"

    # Optional OpenAI-compatible embedding endpoint. Disabled by default so
    # offline and existing TF-IDF deployments never make network requests.
    embedding_enabled: bool = False
    embedding_model: str = ""
    embedding_api_key: str | None = None
    embedding_base_url: str = ""
    embedding_timeout_seconds: int = 15
    cors_allowed_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    sse_max_seconds: int = 600
    sse_poll_interval_seconds: float = 0.5
    task_stale_after_seconds: int = 1800
    # Redis is an optional acceleration layer. SQLite remains the source of truth.
    cache_enabled: bool = False
    redis_url: str = "redis://localhost:6379/0"
    cache_prefix: str = "jm:v1"
    cache_fail_open: bool = True
    redis_socket_connect_timeout_seconds: float = 0.2
    redis_socket_timeout_seconds: float = 0.8
    task_queue_enabled: bool = False
    task_queue_name: str = "jm:tasks"
    task_queue_group: str = "jm:workers"
    task_queue_visibility_timeout_seconds: int = 900

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
