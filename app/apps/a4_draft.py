"""④ 신고서 초안 생성 — 04_draft.yml 대응.

Start → 사례검색 조립 → C-5 과거사례 검색(HTTP, 실패 허용) → 목록 조립
     → 위험도 재산출(LLM) → mode 분기 → 문서생성기(신규 | 재생성) → End
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.cases import fetch_cases
from app.common import NO_QA_FINAL, QaPair, RiskLevel, VisionResult, format_qa_history
from app.llm.gemini import generate_structured

RISK_TEMPERATURE = 0.0
DRAFT_TEMPERATURE = 0.3

# ── 위험도 재산출 (1804000005) ────────────────────────────────────────────
RISK_SYSTEM_PROMPT = """당신은 대학 안전 신고의 위험 등급을 최종 판정하는 담당자입니다.
사진 판독 시점의 잠정 등급과, 그 이후 제보자와 주고받은 질의응답을 함께 보고 등급을 다시 판정합니다.

[판정 규칙]
1. risk_level은 low / medium / high / critical 중 하나입니다.
   - critical: 즉시 인명 사고로 이어질 수 있음
   - high: 중상 가능성이 있음
   - medium: 경상·전도 등 부상 가능성이 있음
   - low: 즉각적 위험은 낮으나 개선이 필요함
2. 질의응답에서 확인된 사실만 등급 조정의 근거로 씁니다.
   - 상향 근거 예: 방치 기간이 길다, 유사 사고가 이미 있었다, 통행량이 많은 구역이다
   - 하향 근거 예: 임시 차단·표지가 이미 설치되어 있다, 접근이 통제된 구역이다
3. 질의응답에서 새로 확인된 사실이 없으면 잠정 등급을 그대로 유지합니다.
4. 제보자가 "잘 모르겠다"고 답한 항목은 등급을 올리는 근거로도 내리는 근거로도 쓰지 않습니다.
5. rationale에는 그렇게 판정한 이유를 개조식 한 문장으로 씁니다.
   잠정 등급과 달라졌다면 무엇이 근거인지 반드시 밝힙니다.
6. changed는 잠정 등급과 최종 등급이 다르면 true, 같으면 false입니다.
7. 반드시 주어진 JSON 스키마 형식으로만 출력합니다."""

RISK_USER_PROMPT = """[신고 위치]
{location_text}

[사진 판독 결과]
- 위험 요소: {detected_hazard}
- 사진 분석: {photo_analysis}
- 위험성: {risk}
- 노출 대상: {exposed_target}
- 잠정 위험 등급: {risk_level}

[질의응답]
{qa_history}"""

RISK_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "risk_level": {
            "type": "string",
            "description": "질의응답까지 반영한 최종 위험 등급",
            "enum": ["low", "medium", "high", "critical"],
        },
        "rationale": {"type": "string", "description": "등급 판정 근거 한 문장 (개조식)"},
        "changed": {"type": "boolean", "description": "잠정 등급과 달라졌으면 true"},
    },
    "required": ["risk_level", "rationale", "changed"],
}

# ── 문서생성기 (1804000009 신규 / 1804000007 재생성) ──────────────────────
DRAFT_SYSTEM_PROMPT = """당신은 대학 안전·보건 제안서를 작성하는 담당자입니다.
신고 위치, 사진 판독 결과, 제보자와의 질의응답 3건을 종합해 신고서 문안을 작성합니다.

[작성 규칙]
1. 육하원칙에 따라 상세하게 쓰되, 반드시 개조식 문장으로 작성합니다.
   - 종결은 "~함", "~임", "~됨", "~요망", "~바람" 형태를 사용합니다.
   - "~습니다", "~합니다" 같은 경어체 종결은 사용하지 않습니다.
2. title: "[위치] [위험 요소] 신고의 건" 형식으로 작성합니다.
3. hazard_place / occurrence_location: 주어진 신고 위치를 그대로 사용합니다. 임의로 바꾸지 않습니다.
4. hazard: 위험 요소를 짧은 명사구로 특정합니다.
5. photo_analysis: 신고서에 "위험 요소 상세"로 인쇄되는 항목입니다.
   사진 판독 결과를 근거로 관찰된 상태를 구체적으로 기술합니다.
   "사진에서 보면", "이미지상" 같이 사진을 가리키는 표현은 쓰지 않습니다 —
   신고서에는 위험 요소 자체에 대한 서술로 읽혀야 합니다.
6. risk: 위험성을 인과관계로 서술합니다. 질의응답에서 얻은 정보(경과 기간, 재발 여부 등)가 있으면 반영합니다.
7. exposed_target: 위험에 노출될 수 있는 대상을 특정합니다.
8. action_urgent: 즉시 취할 수 있는 임시 조치를 제안합니다. (차단, 표지 설치 등)
9. action_fundamental: 원인을 제거하는 항구적 조치를 제안합니다. (교체, 보수, 점검 등)
10. other_opinion: 위 항목에 담기지 않은 참고 사항을 씁니다. 쓸 내용이 없으면 "해당 없음"으로 둡니다.
11. 확인되지 않은 사실을 추측해 단정하지 않습니다. 질의응답에서 "잘 모르겠다"는 답을 받은 내용은 단정하지 않습니다.
12. 제보자의 감정 표현·구어체는 제거하고 사실만 남깁니다.
13. [유사 과거사례]가 주어지면 action_urgent·action_fundamental을 쓸 때 참고합니다.
    - 과거사례의 문장을 그대로 옮기지 않고, 이번 건의 상황에 맞게 다시 씁니다.
    - 이번 건과 무관한 사례는 무시합니다.
    - 과거사례를 근거로 이번 건에서 확인되지 않은 사실을 단정하지 않습니다.
14. 반드시 주어진 JSON 스키마 형식으로만 출력합니다."""

REGENERATE_SUFFIX = """

[재작성 지시]
아래는 직전에 작성했던 초안과, 제보자가 남긴 수정 코멘트입니다.
수정 코멘트를 반영해 초안 **전체**를 다시 작성합니다.
코멘트가 언급하지 않은 항목은 직전 초안의 내용과 취지를 유지하되, 위 작성 규칙은 그대로 지킵니다."""

DRAFT_USER_PROMPT = """[신고 위치]
{location_text}

[제보자 부연 설명]
{description}

[사진 판독 결과]
- 위험 요소: {detected_hazard}
- 사진 분석: {photo_analysis}
- 위험성: {risk}
- 노출 대상: {exposed_target}
- 위험 등급: {risk_level_final}

[질의응답]
{qa_history}

[유사 과거사례]
{cases_text}"""

REGENERATE_USER_SUFFIX = """

[직전 초안]
{previous_draft_json}

[제보자의 수정 코멘트]
{comment}"""

DRAFT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "title": {"type": "string", "description": '"[위치] [위험 요소] 신고의 건" 형식의 제목'},
        "hazard_place": {"type": "string", "description": "위험 발생 장소 (주어진 신고 위치 그대로)"},
        "occurrence_location": {"type": "string", "description": "발생 위치 (주어진 신고 위치 그대로)"},
        "hazard": {"type": "string", "description": "위험 요소 (짧은 명사구)"},
        "photo_analysis": {"type": "string", "description": "위험 요소 상세 — 관찰된 상태 서술"},
        "risk": {"type": "string", "description": "위험성 (인과관계 서술)"},
        "exposed_target": {"type": "string", "description": "위험에 노출될 수 있는 대상"},
        "action_urgent": {"type": "string", "description": "즉시 취할 수 있는 임시 조치"},
        "action_fundamental": {"type": "string", "description": "원인을 제거하는 항구적 조치"},
        "other_opinion": {"type": "string", "description": '기타 의견 — 없으면 "해당 없음"'},
    },
    "required": [
        "title",
        "hazard_place",
        "occurrence_location",
        "hazard",
        "photo_analysis",
        "risk",
        "exposed_target",
        "action_urgent",
        "action_fundamental",
        "other_opinion",
    ],
}


class DraftRequest(BaseModel):
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
    other_opinion: str
    risk_level_final: RiskLevel
    risk_level_rationale: str
    risk_level_changed: bool
    similar_case_ids: list[str]
    similar_case_count: int


def run(req: DraftRequest) -> DraftResponse:
    qa_history = format_qa_history(req.qa_history, req.qa_pairs, empty=NO_QA_FINAL)

    bundle = fetch_cases(
        session_id=req.session_id,
        location_text=req.location_text,
        detected_hazard=req.vision.detected_hazard,
        photo_analysis=req.vision.photo_analysis,
        risk=req.vision.risk,
        top_k=req.top_k,
    )

    # 위험도 재산출
    risk_result = generate_structured(
        node="1804000005 위험도 재산출",
        system_prompt=RISK_SYSTEM_PROMPT,
        user_prompt=RISK_USER_PROMPT.format(
            location_text=req.location_text,
            detected_hazard=req.vision.detected_hazard,
            photo_analysis=req.vision.photo_analysis,
            risk=req.vision.risk,
            exposed_target=req.vision.exposed_target,
            risk_level=req.risk_level,
            qa_history=qa_history,
        ),
        schema=RISK_SCHEMA,
        temperature=RISK_TEMPERATURE,
    )

    # mode 분기
    regenerate = req.mode == "regenerate"
    system_prompt = DRAFT_SYSTEM_PROMPT + (REGENERATE_SUFFIX if regenerate else "")
    user_prompt = DRAFT_USER_PROMPT.format(
        location_text=req.location_text,
        description=req.description,
        detected_hazard=req.vision.detected_hazard,
        photo_analysis=req.vision.photo_analysis,
        risk=req.vision.risk,
        exposed_target=req.vision.exposed_target,
        risk_level_final=risk_result["risk_level"],
        qa_history=qa_history,
        cases_text=bundle.cases_text,
    )
    if regenerate:
        user_prompt += REGENERATE_USER_SUFFIX.format(
            previous_draft_json=req.previous_draft_json or "(없음)",
            comment=req.comment or "(없음)",
        )

    draft = generate_structured(
        node="1804000007 문서생성기 (재생성)" if regenerate else "1804000009 문서생성기 (신규)",
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        schema=DRAFT_SCHEMA,
        temperature=DRAFT_TEMPERATURE,
    )

    return DraftResponse(
        **draft,
        risk_level_final=risk_result["risk_level"],
        risk_level_rationale=risk_result["rationale"],
        risk_level_changed=bool(risk_result["changed"]),
        similar_case_ids=bundle.case_ids,
        similar_case_count=bundle.case_count,
    )


router = APIRouter(tags=["④ 신고서 초안 생성"])


@router.post("/v1/drafts/generate", response_model=DraftResponse)
def generate_draft(req: DraftRequest) -> DraftResponse:
    """유사 과거사례를 참고해 신고서 10개 항목을 쓰고 위험 등급을 재산출한다."""
    return run(req)
