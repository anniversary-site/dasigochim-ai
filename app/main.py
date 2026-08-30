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

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.apps import a1_vision, a2_question, a3_department, a4_draft, a5_insight
from app.backends.base import BackendError
from app.config import get_settings
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


@app.get("/health", tags=["health"])
def health() -> dict:
    return {
        "status": "ok",
        "model": settings.model_name,
        "backend_mode": settings.backend_mode,
        "api_key_loaded": bool(settings.gemini_api_key),
    }


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
