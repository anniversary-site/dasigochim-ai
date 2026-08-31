"""③ 담당 부서 판정 — 03_department.yml 대응.

Start → 검색 요청 조립 → C-1 업무범위 검색(HTTP) → 후보 목록 조립
     → 담당 부서 매칭(LLM) → 부서 확인 요청 조립 → C-2 확인/fallback(HTTP)
     → 판정 결과 파싱 → End

C-1 은 실패하면 그대로 실패시킨다 (부서 판정의 필수 입력).
코드 유효성 검증과 임계값 fallback 은 결정론이라 백엔드(C-2) 몫이다.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app import prompts
from app.backends import get_backend
from app.backends.base import BackendError
from app.common import PromptSelectable, VisionResult, join_query
from app.llm.gemini import generate_structured
from app.llm import tracing

NODE = "department"
DEFAULT_TOP_K = 8


class DepartmentRequest(PromptSelectable):
    session_id: str
    location_text: str
    vision: VisionResult
    top_k: int | None = Field(default=None, description="검색 개수 (비우면 8)")


class DepartmentResponse(BaseModel):
    department_code: str
    department_name: str
    department_contact: str
    assigned_by: str
    confidence: float = Field(description="fallback 이면 -1")
    rationale: str
    llm_confidence: float
    candidate_count: int
    corpus_version: int


def build_candidates_text(candidates: list[dict]) -> str:
    """검색 결과를 프롬프트의 후보 목록 형식으로 편다 (code 노드 1803000004)."""
    blocks = [
        "- department_code: %s (%s)\n  업무 범위: %s"
        % (c.get("code", ""), c.get("name", ""), str(c.get("duty_text") or "").strip())
        for c in candidates
    ]
    return "\n".join(blocks) if blocks else "(검색 결과 없음)"


def run(req: DepartmentRequest) -> DepartmentResponse:
    """API 호출 1회 = Langfuse 트레이스 1개. session_id 로 신고 한 건이 묶인다."""
    with tracing.request_trace(req.session_id, "③ 담당 부서 판정"):
        return _run(req)


def _run(req: DepartmentRequest) -> DepartmentResponse:
    backend = get_backend()

    # 검색 요청 조립 + C-1 업무범위 검색
    query_text = join_query(
        req.vision.detected_hazard,
        req.vision.photo_analysis,
        req.vision.risk,
        req.vision.exposed_target,
        req.location_text,
    )
    search = backend.duty_search(req.session_id, query_text, req.top_k or DEFAULT_TOP_K)

    candidates = search.get("candidates") or []
    candidates_text = build_candidates_text(candidates)
    corpus_version = int(search.get("corpus_version") or 0)

    # 담당 부서 매칭
    prompt = prompts.pick(NODE, req.prompt_versions)
    matched = generate_structured(
        node=f"{prompt.node} [{prompt.label}]",
        system_prompt=prompt.system,
        user_prompt=prompt.render(
            location_text=req.location_text,
            detected_hazard=req.vision.detected_hazard,
            photo_analysis=req.vision.photo_analysis,
            risk=req.vision.risk,
            exposed_target=req.vision.exposed_target,
            candidates_text=candidates_text,
        ),
        schema=prompt.schema,
        temperature=prompt.temperature,
        prompt=prompt,
    )
    llm_confidence = float(matched.get("confidence") or 0.0)

    # C-2 부서 확인 / fallback
    resolved = backend.resolve_department(
        req.session_id,
        str(matched.get("department_code") or "").strip(),
        llm_confidence,
        matched.get("rationale") or "",
    )

    # 판정 결과 파싱 — fallback 이면 confidence 가 null 로 오므로 -1 로 표기한다.
    dept = resolved.get("department") or {}
    if not dept.get("code"):
        raise BackendError(f"departments/resolve 응답에 부서가 없다: {resolved}")
    confidence = resolved.get("confidence")

    return DepartmentResponse(
        department_code=dept.get("code", ""),
        department_name=dept.get("name", ""),
        department_contact=dept.get("contact") or "",
        assigned_by=resolved.get("assigned_by", ""),
        confidence=float(confidence) if confidence is not None else -1.0,
        rationale=resolved.get("rationale", ""),
        llm_confidence=llm_confidence,
        candidate_count=len(candidates),
        corpus_version=corpus_version,
    )


router = APIRouter(tags=["③ 담당 부서 판정"])


@router.post("/v1/departments/classify", response_model=DepartmentResponse)
def classify_department(req: DepartmentRequest) -> DepartmentResponse:
    """업무범위 RAG 로 후보를 뽑아 부서를 고르고, 백엔드가 코드·임계값을 확정한다."""
    return run(req)
