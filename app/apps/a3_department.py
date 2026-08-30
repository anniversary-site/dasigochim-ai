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

from app.backends import get_backend
from app.backends.base import BackendError
from app.common import VisionResult, join_query
from app.llm.gemini import generate_structured

TEMPERATURE = 0.2
DEFAULT_TOP_K = 8

SYSTEM_PROMPT = """당신은 대학의 안전 신고를 담당 부서로 배정하는 분류 담당자입니다.
아래에 부서별 업무 범위 문서에서 검색된 후보 청크가 주어집니다.
이 업무 범위만을 근거로, 제시된 위험 요소를 처리할 부서를 하나 고릅니다.

[판정 규칙]
1. 반드시 주어진 후보 목록에 있는 department_code 중에서만 고릅니다. 목록에 없는 부서를 지어내지 않습니다.
2. rationale에는 어느 업무 범위 문구가 근거인지 인용해 한 문장으로 씁니다.
   이 문장은 신고서 초안에 그대로 노출되므로 제보자가 읽어도 이해할 수 있어야 합니다.
3. confidence는 0.0~1.0 사이의 값으로, 업무 범위 문서가 이 위험 요소를 명확히 포괄하는 정도를 나타냅니다.
   - 업무 범위에 직접 대응하는 문구가 있으면 0.85 이상
   - 유추가 필요하면 0.70~0.85
   - 어느 부서 소관인지 문서로 판단하기 어려우면 0.70 미만
4. 후보 목록이 비어 있으면 department_code를 빈 문자열로 두고 confidence를 0으로 둡니다. 지어내지 않습니다.
5. 반드시 주어진 JSON 스키마 형식으로만 출력합니다."""

USER_PROMPT = """[위험 요소]
- 위치: {location_text}
- 위험 요소: {detected_hazard}
- 사진 분석: {photo_analysis}
- 위험성: {risk}
- 노출 대상: {exposed_target}

[부서 업무 범위 후보]
{candidates_text}"""

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "department_code": {
            "type": "string",
            "description": "후보 목록에 있는 부서 코드. 후보가 없으면 빈 문자열",
        },
        "rationale": {"type": "string", "description": "업무 범위 문구를 인용한 판정 근거 한 문장"},
        "confidence": {
            "type": "number",
            "description": "업무 범위 문서가 이 위험 요소를 포괄하는 정도 (0.0~1.0)",
            "minimum": 0,
            "maximum": 1,
        },
    },
    "required": ["department_code", "rationale", "confidence"],
}


class DepartmentRequest(BaseModel):
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
    matched = generate_structured(
        node="1803000005 담당 부서 매칭",
        system_prompt=SYSTEM_PROMPT,
        user_prompt=USER_PROMPT.format(
            location_text=req.location_text,
            detected_hazard=req.vision.detected_hazard,
            photo_analysis=req.vision.photo_analysis,
            risk=req.vision.risk,
            exposed_target=req.vision.exposed_target,
            candidates_text=candidates_text,
        ),
        schema=SCHEMA,
        temperature=TEMPERATURE,
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
