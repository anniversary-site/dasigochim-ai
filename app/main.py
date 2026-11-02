"""다시고침 AI 서버 — Dify 5개 앱을 5개 REST 엔드포인트로 옮긴 프로토타입.

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
from app.rag import retriever

settings = get_settings()

logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s %(levelname)-5s %(name)s | %(message)s",
)
log = logging.getLogger("app")

app = FastAPI(
    title="다시고침 AI 서버",
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
def check_langfuse_auth() -> None:
    """Langfuse 인증을 기동 시 한 번 실제로 확인한다.

    키가 비어 있지 않다는 것과 인증이 된다는 것은 다르다. 실패해도 기동은 막지 않는다 —
    추적은 부가 기능이고 판독이 먼저다. 대신 로그에 분명히 남겨 조용히 묻히지 않게 한다.
    """
    if not tracing.enabled():
        log.info("langfuse 추적 꺼짐 (키 없음 또는 LANGFUSE_TRACING=false)")
        return
    if tracing.auth_status():
        log.info(
            "langfuse 추적 켜짐 environment=%s mask_pii=%s",
            settings.langfuse_tracing_environment, settings.langfuse_mask_pii,
        )
    else:
        log.error(
            "langfuse 인증 실패 — 트레이스가 전송되지 않는다. "
            "LANGFUSE_PUBLIC_KEY(pk-lf-…)/SECRET_KEY(sk-lf-…) 가 뒤바뀌지 않았는지 확인할 것."
        )


@app.on_event("startup")
def check_style_index() -> None:
    """문체 색인이 실제로 읽히는지 기동 시 확인한다.

    색인이 없어도 ④ 는 돈다 — 문체 참조 없이 쓸 뿐이다. 그래서 **조용히 빠진다.**
    조용한 것이 문제라 여기서 한 줄 남긴다. "왜 v2 때와 문체가 같지?" 를
    로그 첫 화면에서 답할 수 있어야 한다.
    """
    status = retriever.status()
    if not status.get("enabled"):
        log.info("문체 RAG 꺼짐 (RAG_ENABLED=false)")
    elif status.get("indexed"):
        log.info(
            "문체 색인 로드 조각=%d 문서=%d 임베딩=%s 생성=%s",
            status["chunk_count"], len(status["source_docs"]),
            status["embedder"], status["built_at"],
        )
    else:
        log.warning(
            "문체 색인이 없다 (%s) — ④ 는 문체 참조 없이 작성한다. "
            "scripts/build_rag_index.py 로 만들 것.",
            status["index_path"],
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
        # 키가 채워졌는지(ready)가 아니라 실제로 인증되는지(auth). 둘은 다르다 —
        # public/secret 을 뒤바꿔 넣으면 ready 는 true 인데 전송은 401 로 버려진다.
        "langfuse_auth": tracing.auth_status(),
        "langfuse_environment": settings.langfuse_tracing_environment,
        "langfuse_mask_pii": settings.langfuse_mask_pii,
        # ④ 가 문체 견본을 실제로 붙일 수 있는 상태인지. 자세한 내용은 /v1/rag/status
        "style_rag": retriever.status().get("indexed", False),
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


@app.get("/v1/rag/status", tags=["문체 RAG"])
def rag_status() -> dict:
    """문체 색인 상태와 코퍼스에서 집계한 문체 규격."""
    return retriever.status()


@app.get("/v1/rag/style", tags=["문체 RAG"])
def rag_style(query: str = "", top_k: int | None = None) -> dict:
    """④ 프롬프트에 실제로 들어갈 문체 참조 블록을 그대로 돌려준다.

    프롬프트를 손보기 전에 "이 신고에는 어떤 견본이 붙는가" 를 눈으로 보려는 것이다.
    ``query`` 는 ④ 가 만드는 검색어와 같은 형태 — 위험 요소·사진 분석·위험성·위치를
    이어 붙인 한 줄이다.
    """
    bundle = retriever.retrieve(query, top_k=top_k)
    return {
        "source": bundle.source,
        "exemplar_ids": bundle.exemplar_ids,
        "profile": bundle.profile_text,
        "text": bundle.text,
    }


@app.post("/v1/rag/reload", tags=["문체 RAG"])
def rag_reload() -> dict:
    """색인을 다시 만든 뒤 캐시를 비운다 (프롬프트 reload 와 같은 용도)."""
    retriever.reload()
    return retriever.status()


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
