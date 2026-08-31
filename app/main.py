"""안전고래 AI 서버 — Dify 5개 앱을 5개 REST 엔드포인트로 옮긴 프로토타입.

| 엔드포인트                   | 원본 Dify 앱          |
|------------------------------|-----------------------|
| POST /v1/vision/analyze      | ① 사진 판독           |
| POST /v1/questions/next      | ② 확인 질문 생성      |
| POST /v1/departments/classify| ③ 담당 부서 판정      |
| POST /v1/drafts/generate     | ④ 신고서 초안 생성    |
| POST /v1/insights/generate   | ⑤ 요약·인사이트       |
"""

from __future__ import annotations

import logging
import time
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from app import prompts
from app.apps import a1_vision, a2_question, a3_department, a4_draft, a5_insight
from app.backends.base import BackendError
from app.config import get_settings
from app.llm import tracing
from app.llm.gemini import LlmError

settings = get_settings()

logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s %(levelname)-5s %(name)s | %(message)s",
)
log = logging.getLogger("app")

app = FastAPI(
    title="안전고래 AI 서버",
    version="0.1.0",
    description=__doc__,
)

for module in (a1_vision, a2_question, a3_department, a4_draft, a5_insight):
    app.include_router(module.router)


@app.middleware("http")
async def request_log(request: Request, call_next):
    request_id = request.headers.get("X-Request-Id") or uuid.uuid4().hex[:8]
    started = time.perf_counter()
    response = await call_next(request)
    elapsed = time.perf_counter() - started
    log.info(
        "%s %s -> %d (%.2fs) req=%s",
        request.method, request.url.path, response.status_code, elapsed, request_id,
    )
    response.headers["X-Request-Id"] = request_id
    return response


@app.exception_handler(LlmError)
async def handle_llm_error(_: Request, exc: LlmError) -> JSONResponse:
    log.error("LLM 실패: %s", exc)
    return JSONResponse(status_code=502, content={"error": "llm_failed", "message": str(exc)})


@app.exception_handler(BackendError)
async def handle_backend_error(_: Request, exc: BackendError) -> JSONResponse:
    log.error("백엔드 호출 실패: %s", exc)
    return JSONResponse(
        status_code=502,
        content={"error": "backend_failed", "message": str(exc), "upstream_status": exc.status_code},
    )


@app.on_event("startup")
def check_prompts() -> None:
    """활성 프롬프트가 전부 읽히는지 기동 시 확인한다.

    defaults.yaml 오타나 빠진 파일은 첫 호출 때 502 로 나타나면 원인을 찾기 어렵다.
    여기서 터뜨려 기동 자체를 막는다.
    """
    prompts.verify_all()


@app.on_event("shutdown")
def flush_tracing() -> None:
    """Langfuse 는 이벤트를 모아 보낸다. 종료 시 밀어 넣지 않으면 마지막 몇 건이 사라진다."""
    tracing.flush()


@app.get("/health", tags=["health"])
def health() -> dict:
    return {
        "status": "ok",
        "model": settings.model_name,
        "backend_mode": settings.backend_mode,
        "api_key_loaded": bool(settings.gemini_api_key),
        "prompt_source": settings.prompt_source,
        "langfuse_ready": settings.langfuse_ready,
        "langfuse_tracing": tracing.enabled(),
    }


@app.get("/v1/prompts", tags=["프롬프트 관리"])
def list_prompts() -> dict:
    """노드별 활성 버전과 고를 수 있는 버전 목록.

    여기 나온 버전을 각 API 요청 본문의 `prompt_versions` 에 넣으면
    서버 재시작 없이 그 호출에만 다른 프롬프트를 쓸 수 있다.
    """
    return {"source": settings.prompt_source, "nodes": prompts.catalog()}


@app.get("/v1/prompts/{name}/{version}", tags=["프롬프트 관리"])
def show_prompt(name: str, version: str) -> dict:
    """프롬프트 한 버전의 내용을 그대로 돌려준다 (실험 중 눈으로 확인용)."""
    try:
        prompt = prompts.load(name, version)
    except prompts.PromptError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "name": prompt.name,
        "version": prompt.version,
        "node": prompt.node,
        "source": prompt.source,
        "temperature": prompt.temperature,
        "changelog": prompt.changelog,
        "system": prompt.system,
        "user": prompt.user,
        "schema": prompt.schema,
    }


@app.post("/v1/prompts/reload", tags=["프롬프트 관리"])
def reload_prompts() -> dict:
    """YAML 을 고친 뒤 캐시를 비운다. 서버를 다시 띄우지 않고 반영하려는 것."""
    prompts.reload()
    prompts.verify_all()
    return {"status": "reloaded", "nodes": prompts.catalog()}


def main() -> None:
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
        reload=False,
    )


if __name__ == "__main__":
    main()
