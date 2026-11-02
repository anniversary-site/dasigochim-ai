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

    # ⑤ 반복 발생 판정. 집계 자체는 백엔드 SQL 이 하고, 기간과 임계값은 AI 서버가 정해
    # C-5 요청에 실어 보낸다 — 인사이트 문장의 성격을 좌우하므로 프롬프트와 함께 튜닝한다.
    case_window_days: int = 30
    recurring_threshold: int = 3  # 이번 건을 포함한 동일 건물 신고 횟수

    # ② 객체 확인 질문. ① 의 object_confidence 가 이 값 미만이면 첫 질문을
    # "이 객체가 맞습니까" 로 바꾼다. 판정은 LLM 이 아니라 이 임계값이 한다.
    object_confirm_threshold: float = 0.7

    # ── ④ 문체 RAG (app/rag) ──────────────────────────────────────────
    # docs/*.pdf 재해조사의견서에서 '쓰는 법' 만 뽑아 draft 프롬프트에 붙인다.
    # 색인이 없으면 조용히 문체 참조 없이 작성한다 — 초안 생성을 막지 않는다.
    rag_enabled: bool = True
    rag_index_path: str = ""   # 비우면 app/rag/index/style_index.json
    rag_docs_dir: str = ""     # 비우면 <ai-server>/docs (색인 빌드 때만 쓴다)
    # 역할(관찰·위험·원인·조치)마다 붙일 견본 수. 늘릴수록 문체는 선명해지지만
    # 눈앞의 문장을 베낄 여지도 함께 늘어난다. 2 는 그 사이에서 고른 값이다.
    rag_top_k_per_role: int = 2
    rag_max_chars_per_exemplar: int = 260
    embedding_model: str = "gemini-embedding-001"
    embedding_dimensions: int = 768

    # ── 프롬프트 소스 ──────────────────────────────────────────────────
    # file     : app/prompts/<노드>/<버전>.yaml (git 이 진실. 오프라인·CI 기본값)
    # langfuse : Langfuse 프롬프트 관리. 실패하면 file 로 자동 폴백한다.
    prompt_source: str = "file"
    # 노드별 버전 강제. "vision=v2,question=v1" 형식. 요청 본문이 이보다 우선한다.
    prompt_versions: str = ""
    prompt_cache_ttl_seconds: int = 60

    # ── Langfuse ──────────────────────────────────────────────────────
    # .env 는 pydantic-settings 가 읽을 뿐 os.environ 에 넣지 않는다.
    # 따라서 SDK 가 환경변수를 스스로 못 읽으므로 여기서 받아 명시적으로 넘긴다.
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_base_url: str = "https://cloud.langfuse.com"
    langfuse_prompt_prefix: str = "dasigochim"
    langfuse_tracing: bool = True
    # 트레이스가 쌓이는 환경 이름. 로컬·시연·운영이 한 프로젝트에 섞이면
    # "운영에서 품질이 떨어졌다" 를 볼 수 없다. Langfuse UI 에서 이 값으로 거른다.
    # SDK 가 스스로 읽는 환경변수 이름과 같게 두어 어느 쪽으로 넣어도 통하게 한다.
    langfuse_tracing_environment: str = "local"
    # 트레이스로 나가는 값에서 연락처·이메일 등 구조화된 개인정보를 지운다.
    # 위치와 위험 요소 서술은 남긴다 — 그게 없으면 트레이스를 볼 이유가 없다.
    langfuse_mask_pii: bool = True

    # 서버
    host: str = "127.0.0.1"
    port: int = 8000
    log_level: str = "info"

    @property
    def langfuse_ready(self) -> bool:
        return bool(self.langfuse_public_key and self.langfuse_secret_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
