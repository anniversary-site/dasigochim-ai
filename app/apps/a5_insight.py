"""⑤ 요약·인사이트 — 05_case_insight.yml 대응.

Start → 사례검색 조립 → C-5(HTTP, 실패 허용) → 목록·집계 조립
     → 제목·요약(LLM) → 인사이트(LLM) → End

초안 확정 후 비동기로 실행한다. 사용자 대면 경로에 지연을 주지 않는다.

반복 발생 여부는 **LLM 이 판단하지 않는다.** '최근 N일 동일 건물 K건' 은 백엔드
SQL 집계로 나오는 확정 사실이고, 임계값(RECURRING_THRESHOLD) 비교로 결정한다.
LLM 은 그 사실을 담당자용 문장으로 풀어 쓰는 일만 한다 — 숫자를 지어낼 여지를 없앤다.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.cases import CaseStats, fetch_cases
from app.common import RiskLevel
from app.config import get_settings
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
이번 신고서 초안과, 같은 건물에서 접수된 과거 신고 사례, 그리고 데이터베이스 집계 결과를
함께 보고 담당자용 인사이트를 씁니다. 이 글은 제보자에게 보이지 않는 내부용입니다.

[작성 규칙]
1. insight: 담당자가 판단에 쓸 내용을 개조식 2~4문장으로 씁니다.
   - 반복 발생 통계에 건수가 잡혔다면, 무엇이 반복되는지와 지난 조치가 왜 충분하지 않았는지를 짚습니다.
   - 미해결 건이 남아 있다면 그 사실을 반드시 언급합니다.
   - 반복이 아니면 이번 건에서 특히 주의할 점을 짚습니다.
   - 개조식("~함", "~임", "~필요함")으로 쓰고 마침표로 끝맺습니다.
     목록 기호(-, •, 1.)나 줄바꿈을 넣지 않습니다.
2. recommended_priority: immediate / high / normal / low 중 하나입니다.
   위험 등급과 반복 발생 통계를 함께 고려합니다.
3. related_report_ids: 인사이트의 근거로 실제 인용한 과거사례의 report_id만 담습니다. 없으면 빈 배열입니다.
4. **통계의 각 수치는 대괄호에 적힌 범위에만 해당합니다.**
   서로 다른 범위의 수치를 합치거나, 한 범위의 수치를 다른 범위에 옮겨 쓰지 않습니다.
   - 잘못된 예: [김대건관 전체] 3건인데 "김대건관 2층에서 3건 발생" 이라고 쓰는 것
   - 잘못된 예: 미해결 2건이 어느 층인지 통계에 없는데 "2층 미해결 2건" 이라고 쓰는 것
   - 수치를 인용할 때는 범위를 함께 씁니다. 예: "최근 30일 김대건관 전체 3건"
   통계는 데이터베이스 집계 결과이므로 다시 세거나 고쳐 쓰지 않습니다.
   집계가 없다고 표시되면 반복 여부를 단정하지 않습니다.
5. 과거사례와 통계에서 확인되는 사실만 씁니다. 사례가 없으면 없다고 쓰고 추측하지 않습니다.
   과거사례 목록은 집계 기간 밖의 건도 포함될 수 있으므로, 목록의 건수를 세어 통계처럼 쓰지 않습니다.
6. 조치를 지시하지 않습니다. 판단의 근거를 제공하는 것이 역할입니다.
7. 반드시 주어진 JSON 스키마 형식으로만 출력합니다."""

INSIGHT_USER_PROMPT = """[이번 신고]
- 위치: {location_text}
- 위험 요소: {detected_hazard}
- 확정 위험 등급: {risk_level}
- 요약: {summary}

[신고서 초안]
{draft_json}

[반복 발생 통계 — 데이터베이스 집계 결과이며 확정 사실임]
{stats_text}

[관련 과거사례]
{cases_text}"""

# is_recurring 은 여기 없다 — 집계 수치로 코드가 결정한다 (모듈 docstring 참고).
INSIGHT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
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
    "required": ["insight", "recommended_priority", "related_report_ids"],
}


class InsightRequest(BaseModel):
    session_id: str
    report_id: str | None = Field(default=None, description="report_public_id. 집계·목록에서 이번 건을 뺀다")
    location_text: str
    detected_hazard: str
    photo_analysis: str
    risk: str
    risk_level: RiskLevel = Field(description="확정 위험 등급")
    draft_json: str = Field(description="확정된 신고서 초안 JSON 문자열")
    top_k: int | None = None

    # 반복 발생 집계 대상. 백엔드가 reports 행에서 그대로 채워 준다.
    # 없으면(위치 미상) 집계를 건너뛰고 목록만 받는다.
    building_id: int | None = Field(default=None, description="집계 대상 건물 id (백엔드 buildings.id)")
    building_name: str | None = Field(default=None, description="집계 대상 건물명 (building_name_snapshot)")
    floor: str | None = Field(default=None, description="이번 신고의 층")


class InsightResponse(BaseModel):
    summary: str
    keywords: list[str]
    is_recurring: bool = Field(description="반복 발생 여부. LLM 판단이 아니라 집계 수치로 결정된다")
    insight: str
    recommended_priority: Literal["immediate", "high", "normal", "low"]
    related_report_ids: list[str]
    similar_case_ids: list[str]
    similar_case_count: int
    recurrence: CaseStats | None = Field(
        default=None,
        description="is_recurring 의 근거가 된 집계 원본. 관리자 화면에 그대로 노출할 수 있다",
    )


def run(req: InsightRequest) -> InsightResponse:
    bundle = fetch_cases(
        session_id=req.session_id,
        location_text=req.location_text,
        detected_hazard=req.detected_hazard,
        photo_analysis=req.photo_analysis,
        risk=req.risk,
        top_k=req.top_k,
        report_id=req.report_id,
        building_id=req.building_id,
        building_name=req.building_name,
        floor=req.floor,
    )
    is_recurring = bundle.is_recurring(get_settings().recurring_threshold)

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
            stats_text=bundle.stats_text,
            cases_text=bundle.cases_text,
        ),
        schema=INSIGHT_SCHEMA,
        temperature=INSIGHT_TEMPERATURE,
    )

    return InsightResponse(
        summary=summary["summary"],
        keywords=summary["keywords"],
        is_recurring=is_recurring,
        insight=insight["insight"],
        recommended_priority=insight["recommended_priority"],
        related_report_ids=insight["related_report_ids"],
        similar_case_ids=bundle.case_ids,
        similar_case_count=bundle.case_count,
        recurrence=bundle.stats,
    )


router = APIRouter(tags=["⑤ 요약·인사이트"])


@router.post("/v1/insights/generate", response_model=InsightResponse)
def generate_insight(req: InsightRequest) -> InsightResponse:
    """담당 부서용 요약·키워드와 과거사례 기반 반복 발생 인사이트를 만든다."""
    return run(req)
