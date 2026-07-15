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

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
