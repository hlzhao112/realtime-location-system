from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "trolley-tracking"
    secret_key: str = "change-me-in-production-omada-2026"
    access_token_expire_hours: int = 12
    admin_username: str = "admin"
    admin_password: str = "omada2026"

    database_url: str = "sqlite:///./trolley.db"
    redis_url: str = "redis://localhost:6379/0"

    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:5174,http://127.0.0.1:5174"

    ingest_rate_per_project: int = 200
    ingest_stream: str = "ingest.raw"
    ingest_stream_maxlen: int = 100_000
    ingest_dedup_ttl: int = 600
    ingest_max_epcs: int = 32

    templates_dir: Path = ROOT / "templates"

    @property
    def cors_origin_list(self) -> list[str]:
        return [x.strip() for x in self.cors_origins.split(",") if x.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
