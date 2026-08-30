"""환경 설정. Dify 의 env.* 변수와 1:1 로 대응한다."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

AI_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=AI_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # LLM
    gemini_api_key: str = ""
    model_name: str = "gemini-3.1-flash-lite"
    thinking_level: str = "low"
    llm_timeout_seconds: float = 60.0
    llm_max_retries: int = 2

    # 백엔드 (env.BACKEND_BASE_URL / env.AGENT_TOKEN)
    backend_mode: str = "stub"
    backend_base_url: str = "http://localhost:8080"
    agent_token: str = "dev-agent-token"
    backend_timeout_seconds: float = 10.0

    # stub 모드 부서 판정 정책. http 모드에서는 백엔드(C-2)가 결정한다.
    department_confidence_threshold: float = 0.7
    default_department_code: str = "SAFETY_CENTER"

    # 서버
    host: str = "127.0.0.1"
    port: int = 8000
    log_level: str = "info"


@lru_cache
def get_settings() -> Settings:
    return Settings()
