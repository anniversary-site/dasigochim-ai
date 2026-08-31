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

from app import prompts
from app.cases import CaseStats, fetch_cases
from app.common import PromptSelectable, RiskLevel
from app.config import get_settings
from app.llm.gemini import generate_structured
from app.llm import tracing

SUMMARY_NODE = "summary"
INSIGHT_NODE = "insight"


class InsightRequest(PromptSelectable):
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
    """API 호출 1회 = Langfuse 트레이스 1개. session_id 로 신고 한 건이 묶인다."""
    with tracing.request_trace(req.session_id, "⑤ 요약·인사이트"):
        return _run(req)


def _run(req: InsightRequest) -> InsightResponse:
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

    summary_prompt = prompts.pick(SUMMARY_NODE, req.prompt_versions)
    summary = generate_structured(
        node=f"{summary_prompt.node} [{summary_prompt.label}]",
        system_prompt=summary_prompt.system,
        user_prompt=summary_prompt.render(draft_json=req.draft_json),
        schema=summary_prompt.schema,
        temperature=summary_prompt.temperature,
        prompt=summary_prompt,
    )

    insight_prompt = prompts.pick(INSIGHT_NODE, req.prompt_versions)
    insight = generate_structured(
        node=f"{insight_prompt.node} [{insight_prompt.label}]",
        system_prompt=insight_prompt.system,
        user_prompt=insight_prompt.render(
            location_text=req.location_text,
            detected_hazard=req.detected_hazard,
            risk_level=req.risk_level,
            summary=summary["summary"],
            draft_json=req.draft_json,
            stats_text=bundle.stats_text,
            cases_text=bundle.cases_text,
        ),
        schema=insight_prompt.schema,
        temperature=insight_prompt.temperature,
        prompt=insight_prompt,
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
