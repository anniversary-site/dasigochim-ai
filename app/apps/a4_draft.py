"""④ 신고서 초안 생성 — 04_draft.yml 대응.

Start → 사례검색 조립 → C-5 과거사례 검색(HTTP, 실패 허용) → 목록 조립
     → 문체 견본 검색(RAG, 실패 허용) → 위험도 재산출(LLM)
     → mode 분기 → 문서생성기(신규 | 재생성) → End

프롬프트에 들어가는 참고 자료는 **성격이 다른 두 종류**다. 섞이면 안 된다.

    [유사 과거사례]  C-5 · 우리 캠퍼스의 실제 신고 기록  → 내용을 참고해도 된다
    [문체 참조]      app/rag · 남의 재해조사의견서 발췌  → 문장 골격만. 내용 차용은 오답

두 블록은 프롬프트에서 라벨과 규칙(draft/v3 규칙 14·15)으로 갈라 놓는다.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app import prompts
from app.cases import fetch_cases
from app.common import (
    NO_QA_FINAL,
    PromptSelectable,
    QaPair,
    RiskLevel,
    VisionResult,
    format_qa_history,
    join_query,
)
from app.llm.gemini import generate_structured
from app.llm import tracing
from app.rag import retriever

RISK_NODE = "risk"
DRAFT_NODE = "draft"

# 이 자리표시자가 있는 프롬프트 버전에서만 문체 검색을 돈다. v1·v2 는 없으므로
# 되돌려도 임베딩 호출이 한 번 낭비되지 않는다.
STYLE_PLACEHOLDER = "{style_reference}"


class DraftRequest(PromptSelectable):
    session_id: str
    mode: Literal["generate", "regenerate"] = "generate"
    location_text: str
    description: str
    vision: VisionResult
    risk_level: RiskLevel = Field(description="판독 시점의 잠정 위험 등급")
    qa_history: str | None = None
    qa_pairs: list[QaPair] | None = None
    previous_draft_json: str | None = Field(default=None, description="regenerate 전용")
    comment: str | None = Field(default=None, description="regenerate 전용 수정 코멘트")
    top_k: int | None = Field(default=None, description="과거사례 검색 개수 (비우면 4)")

    # 과거사례 목록의 관련성 기준. 1단계에는 텍스트 검색이 없어 위치가 유일한 신호다
    # (2단계에서 임베딩 유사도가 붙으면 이 값들은 보조 필터가 된다).
    building_id: int | None = Field(default=None, description="신고 건물 id (백엔드 buildings.id)")
    building_name: str | None = Field(default=None, description="신고 건물명 (building_name_snapshot)")
    floor: str | None = Field(default=None, description="신고 층")


class DraftResponse(BaseModel):
    title: str
    hazard_place: str
    occurrence_location: str
    hazard: str
    photo_analysis: str
    risk: str
    exposed_target: str
    action_urgent: str
    action_fundamental: str
    risk_level_final: RiskLevel
    risk_level_rationale: str
    risk_level_changed: bool
    similar_case_ids: list[str]
    similar_case_count: int
    # 어떤 문체 견본이 붙었는지. 초안이 이상할 때 "무엇을 보고 그렇게 썼는가" 를
    # 되짚을 수 있어야 한다 — similar_case_ids 를 남기는 이유와 같다.
    style_reference_ids: list[str] = Field(
        default_factory=list, description="이번 초안에 붙은 문체 견본 조각 id"
    )
    style_reference_source: str = Field(
        default="none", description="문체 견본 선택 방식 (embedding | role-order | none)"
    )


def run(req: DraftRequest) -> DraftResponse:
    """API 호출 1회 = Langfuse 트레이스 1개. session_id 로 신고 한 건이 묶인다."""
    with tracing.request_trace(req.session_id, "④ 신고서 초안 생성"):
        return _run(req)


def _run(req: DraftRequest) -> DraftResponse:
    qa_history = format_qa_history(req.qa_history, req.qa_pairs, empty=NO_QA_FINAL)

    bundle = fetch_cases(
        session_id=req.session_id,
        location_text=req.location_text,
        detected_hazard=req.vision.detected_hazard,
        photo_analysis=req.vision.photo_analysis,
        risk=req.vision.risk,
        top_k=req.top_k,
        building_id=req.building_id,
        building_name=req.building_name,
        floor=req.floor,
    )

    # 위험도 재산출
    risk_prompt = prompts.pick(RISK_NODE, req.prompt_versions)
    risk_result = generate_structured(
        node=f"{risk_prompt.node} [{risk_prompt.label}]",
        system_prompt=risk_prompt.system,
        user_prompt=risk_prompt.render(
            location_text=req.location_text,
            detected_hazard=req.vision.detected_hazard,
            photo_analysis=req.vision.photo_analysis,
            risk=req.vision.risk,
            exposed_target=req.vision.exposed_target,
            risk_level=req.risk_level,
            qa_history=qa_history,
        ),
        schema=risk_prompt.schema,
        temperature=risk_prompt.temperature,
        prompt=risk_prompt,
    )

    # mode 분기. 재생성 접미사는 draft 프롬프트 파일에 같이 들어 있다 —
    # 본문과 접미사가 따로 놀면 버전을 되돌릴 때 짝이 어긋난다.
    regenerate = req.mode == "regenerate"
    draft_prompt = prompts.pick(DRAFT_NODE, req.prompt_versions)

    # 문체 견본. 과거사례와 같은 태도로 다룬다 — 부가 정보이므로 실패해도 초안을 막지 않고
    # 안내 문구로 우회한다 (retriever 가 예외를 밖으로 내보내지 않는다).
    style = (
        retriever.retrieve(
            join_query(
                req.vision.detected_hazard,
                req.vision.photo_analysis,
                req.vision.risk,
                req.location_text,
            )
        )
        if STYLE_PLACEHOLDER in draft_prompt.user
        else retriever.StyleBundle(text=retriever.DISABLED_TEXT)
    )

    system_prompt = draft_prompt.system + (
        draft_prompt.extra("regenerate_system_suffix") if regenerate else ""
    )
    user_prompt = draft_prompt.render(
        location_text=req.location_text,
        description=req.description,
        detected_hazard=req.vision.detected_hazard,
        photo_analysis=req.vision.photo_analysis,
        risk=req.vision.risk,
        exposed_target=req.vision.exposed_target,
        risk_level_final=risk_result["risk_level"],
        qa_history=qa_history,
        cases_text=bundle.cases_text,
        style_reference=style.text,
    )
    if regenerate:
        user_prompt += draft_prompt.extra("regenerate_user_suffix").format(
            previous_draft_json=req.previous_draft_json or "(없음)",
            comment=req.comment or "(없음)",
        )

    # YAML 블록 스칼라(|)라 끝에 줄바꿈이 붙는다. 이 값은 로그 한 줄과 Langfuse
    # generation 이름이 되므로 그대로 두면 이름이 두 줄로 쪼개진다.
    node_label = (
        draft_prompt.extra("regenerate_node", draft_prompt.node).strip()
        if regenerate
        else draft_prompt.node
    )
    draft = generate_structured(
        node=f"{node_label} [{draft_prompt.label}]",
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        schema=draft_prompt.schema,
        temperature=draft_prompt.temperature,
        prompt=draft_prompt,
    )

    return DraftResponse(
        **draft,
        risk_level_final=risk_result["risk_level"],
        risk_level_rationale=risk_result["rationale"],
        risk_level_changed=bool(risk_result["changed"]),
        similar_case_ids=bundle.case_ids,
        similar_case_count=bundle.case_count,
        style_reference_ids=style.exemplar_ids,
        style_reference_source=style.source,
    )


router = APIRouter(tags=["④ 신고서 초안 생성"])


@router.post("/v1/drafts/generate", response_model=DraftResponse)
def generate_draft(req: DraftRequest) -> DraftResponse:
    """유사 과거사례를 참고해 신고서 10개 항목을 쓰고 위험 등급을 재산출한다."""
    return run(req)
