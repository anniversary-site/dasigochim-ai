"""⑤ 요약·인사이트 — 05_case_insight.yml 대응.

Start → 사례검색 조립 → C-5(HTTP, 실패 허용) → 목록 조립
     → 제목·요약(LLM) → 인사이트(LLM) → End

초안 확정 후 비동기로 실행한다. 사용자 대면 경로에 지연을 주지 않는다.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.cases import fetch_cases
from app.common import RiskLevel
from app.llm.gemini import generate_structured

SUMMARY_TEMPERATURE = 0.4
INSIGHT_TEMPERATURE = 0.5

SUMMARY_SYSTEM_PROMPT = """당신은 접수된 안전 신고를 담당 부서 담당자가 빠르게 훑을 수 있도록 정리하는 담당자입니다.
확정된 신고서 초안을 읽고 목록·알림에 쓸 짧은 요약을 만듭니다.

[작성 규칙]
1. summary: 두 문장 이내. 무엇이 어디서 어떻게 위험한지가 한눈에 들어와야 합니다.
   개조식("~함", "~임")으로 씁니다.
2. keywords: 검색·분류에 쓸 키워드 3~5개. 명사 위주의 짧은 단어로 씁니다.
   장소·위험 요소·조치 유형이 고루 들어가게 합니다.
3. 초안에 없는 사실을 새로 만들지 않습니다. 요약은 초안의 범위를 넘지 않습니다.
4. 반드시 주어진 JSON 스키마 형식으로만 출력합니다."""

SUMMARY_USER_PROMPT = """[확정된 신고서 초안]
{draft_json}"""

SUMMARY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": {"type": "string", "description": "목록·알림용 두 문장 이내 개조식 요약"},
        "keywords": {
            "type": "array",
            "description": "검색·분류용 키워드 3~5개",
            "minItems": 3,
            "maxItems": 5,
            "items": {"type": "string"},
        },
    },
    "required": ["summary", "keywords"],
}

INSIGHT_SYSTEM_PROMPT = """당신은 대학 안전·보건 담당자에게 이번 신고를 어떻게 다룰지 조언하는 분석가입니다.
이번 신고서 초안과, 과거에 접수된 유사 신고 사례를 함께 보고 담당자용 인사이트를 씁니다.
이 글은 제보자에게 보이지 않는 내부용입니다.

[작성 규칙]
1. is_recurring: 유사 과거사례가 같은 장소 또는 같은 유형의 반복 발생을 보여주면 true입니다.
   유사 사례가 없거나 관련이 약하면 false입니다.
2. insight: 담당자가 판단에 쓸 내용을 개조식 2~4문장으로 씁니다.
   - 반복 발생이면 무엇이 반복되는지, 지난 조치가 왜 충분하지 않았는지 짚습니다.
   - 반복이 아니면 이번 건에서 특히 주의할 점을 짚습니다.
3. recommended_priority: immediate / high / normal / low 중 하나입니다.
   위험 등급과 반복 여부를 함께 고려합니다.
4. related_report_ids: 인사이트의 근거로 실제 인용한 과거사례의 report_id만 담습니다. 없으면 빈 배열입니다.
5. 과거사례에서 확인되는 사실만 씁니다. 사례가 없으면 없다고 쓰고 추측하지 않습니다.
6. 조치를 지시하지 않습니다. 판단의 근거를 제공하는 것이 역할입니다.
7. 반드시 주어진 JSON 스키마 형식으로만 출력합니다."""

INSIGHT_USER_PROMPT = """[이번 신고]
- 위치: {location_text}
- 위험 요소: {detected_hazard}
- 확정 위험 등급: {risk_level}
- 요약: {summary}

[신고서 초안]
{draft_json}

[유사 과거사례]
{cases_text}"""

INSIGHT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "is_recurring": {"type": "boolean", "description": "같은 장소·유형의 반복 발생이면 true"},
        "insight": {"type": "string", "description": "담당자용 판단 근거 2~4문장 (개조식)"},
        "recommended_priority": {
            "type": "string",
            "description": "권고 처리 우선순위",
            "enum": ["immediate", "high", "normal", "low"],
        },
        "related_report_ids": {
            "type": "array",
            "description": "근거로 인용한 과거사례 report_id",
            "items": {"type": "string"},
        },
    },
    "required": ["is_recurring", "insight", "recommended_priority", "related_report_ids"],
}


class InsightRequest(BaseModel):
    session_id: str
    report_id: str | None = Field(default=None, description="report_public_id")
    location_text: str
    detected_hazard: str
    photo_analysis: str
    risk: str
    risk_level: RiskLevel = Field(description="확정 위험 등급")
    draft_json: str = Field(description="확정된 신고서 초안 JSON 문자열")
    top_k: int | None = None


class InsightResponse(BaseModel):
    summary: str
    keywords: list[str]
    is_recurring: bool
    insight: str
    recommended_priority: Literal["immediate", "high", "normal", "low"]
    related_report_ids: list[str]
    similar_case_ids: list[str]
    similar_case_count: int


def run(req: InsightRequest) -> InsightResponse:
    bundle = fetch_cases(
        session_id=req.session_id,
        location_text=req.location_text,
        detected_hazard=req.detected_hazard,
        photo_analysis=req.photo_analysis,
        risk=req.risk,
        top_k=req.top_k,
    )

    summary = generate_structured(
        node="1805000005 제목·요약",
        system_prompt=SUMMARY_SYSTEM_PROMPT,
        user_prompt=SUMMARY_USER_PROMPT.format(draft_json=req.draft_json),
        schema=SUMMARY_SCHEMA,
        temperature=SUMMARY_TEMPERATURE,
    )

    insight = generate_structured(
        node="1805000006 인사이트",
        system_prompt=INSIGHT_SYSTEM_PROMPT,
        user_prompt=INSIGHT_USER_PROMPT.format(
            location_text=req.location_text,
            detected_hazard=req.detected_hazard,
            risk_level=req.risk_level,
            summary=summary["summary"],
            draft_json=req.draft_json,
            cases_text=bundle.cases_text,
        ),
        schema=INSIGHT_SCHEMA,
        temperature=INSIGHT_TEMPERATURE,
    )

    return InsightResponse(
        summary=summary["summary"],
        keywords=summary["keywords"],
        is_recurring=bool(insight["is_recurring"]),
        insight=insight["insight"],
        recommended_priority=insight["recommended_priority"],
        related_report_ids=insight["related_report_ids"],
        similar_case_ids=bundle.case_ids,
        similar_case_count=bundle.case_count,
    )


router = APIRouter(tags=["⑤ 요약·인사이트"])


@router.post("/v1/insights/generate", response_model=InsightResponse)
def generate_insight(req: InsightRequest) -> InsightResponse:
    """담당 부서용 요약·키워드와 과거사례 기반 반복 발생 인사이트를 만든다."""
    return run(req)
