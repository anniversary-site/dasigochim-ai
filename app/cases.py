"""C-5 과거사례 조회 + 프롬프트 재료 조립. ④ 와 ⑤ 가 같은 코드를 쓴다.

응답은 성격이 다른 두 덩어리로 나뉜다.

- ``cases``  — 관련 과거 신고 목록. 1단계는 '동일 건물 최근순', 2단계에서 임베딩
               유사도 순으로 바뀐다. 순위 기준만 바뀌고 계약은 그대로다.
- ``stats``  — 동일 건물·기간 집계. **순수 SQL 이라 LLM 도 임베딩도 끼지 않는다.**
               '최근 30일 김대건관 3건' 같은 확정 사실이며, 반복 발생 판정의 근거다.

과거사례는 부가 정보다. 조회에 실패해도 초안 작성을 막지 않고
'(과거사례 조회 실패 — 참고 없이 작성)' 으로 우회한다
(Dify HTTP 노드의 error_strategy: default-value 대응).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from app.backends import get_backend
from app.common import join_query
from app.config import get_settings

log = logging.getLogger(__name__)

DEFAULT_TOP_K = 4
FAILED_TEXT = "(과거사례 조회 실패 — 참고 없이 작성)"
EMPTY_TEXT = "(유사 과거사례 없음)"
NO_STATS_TEXT = "(위치 정보가 없어 반복 발생 집계를 하지 않음)"
STATS_FAILED_TEXT = "(반복 발생 집계 실패 — 반복 여부를 판단하지 않음)"

STATUS_LABELS = {"RECEIVED": "접수", "REVIEWING": "처리중", "RESOLVED": "처리완료"}
NO_ACTION_TEXT = "(미조치 — 처리 진행 중)"


class CaseStats(BaseModel):
    """동일 건물·기간 집계. 백엔드 SQL 이 센 값이라 그대로 인용할 수 있다.

    ``total`` 은 **이번 신고를 제외한** 과거 신고 건수다.
    """

    window_days: int = Field(description="집계 기간 (일)")
    building_name: str | None = Field(default=None, description="집계 대상 건물명")
    floor: str | None = Field(default=None, description="이번 신고의 층")
    total: int = Field(default=0, description="기간 내 동일 건물 신고 건수 (이번 건 제외)")
    unresolved: int = Field(default=0, description="그중 미해결(접수·처리중) 건수")
    same_floor: int = Field(default=0, description="그중 동일 층 건수")
    risk_distribution: dict[str, int] = Field(default_factory=dict, description="위험 등급별 건수")
    first_occurred_at: str | None = Field(default=None, description="기간 내 최초 발생일 (YYYY-MM-DD)")


@dataclass
class CaseBundle:
    cases_text: str
    stats_text: str
    case_count: int = 0
    case_ids: list[str] = field(default_factory=list)
    stats: CaseStats | None = None

    def is_recurring(self, threshold: int) -> bool:
        """반복 발생 판정. LLM 이 아니라 집계 수치가 정한다.

        임계값은 '이번 건을 포함한' 횟수라서 total 에 1 을 더해 비교한다.
        집계가 없으면(위치 미상·조회 실패) 판단하지 않는다 — false 로 둔다.
        """
        if self.stats is None:
            return False
        return self.stats.total + 1 >= threshold


def _format_case(case: dict[str, Any]) -> str:
    where = " ".join(
        part for part in (case.get("building_name"), _floor_label(case.get("floor"))) if part
    )
    head = [str(case.get("occurred_at") or "일자미상")]
    if where:
        head.append(where)
    status = STATUS_LABELS.get(str(case.get("status") or ""), "")
    if status:
        head.append(status)
    score = case.get("score")
    if score is not None:
        head.append(f"유사도 {float(score):.2f}")

    return (
        f"- report_id: {case.get('report_id') or ''} ({', '.join(head)})\n"
        f"  위험 요소: {case.get('hazard') or ''}\n"
        f"  당시 근본 조치: {case.get('action_fundamental') or NO_ACTION_TEXT}"
    )


def _floor_label(floor: Any) -> str:
    text = str(floor or "").strip()
    if not text:
        return ""
    return text if text.upper().startswith("B") else f"{text}층"


def _format_stats(stats: CaseStats) -> str:
    """집계를 프롬프트용 텍스트로 만든다.

    **모든 수치에 적용 범위를 명시한다.** 범위를 안 적으면 모델이 '건물 전체 3건' 과
    '2층 2건' 을 뒤섞어 "2층에서 3건" 같은 문장을 만든다 — SQL 로 몰아낸 환각이
    서술 단계로 되돌아오는 경로다. 실제로 그렇게 나와서 이 형식으로 바꿨다.
    """
    building = stats.building_name or "동일 건물"
    lines = [f"- 집계 기간: 최근 {stats.window_days}일 (오늘 기준, 이번 신고는 제외한 수치임)"]

    if stats.total <= 0:
        lines.append(f"- [{building} 전체] 0건 — 기간 내 동일 건물 신고 없음")
        return "\n".join(lines)

    lines.append(f"- [{building} 전체] {stats.total}건 (이 중 미해결 {stats.unresolved}건)")
    if stats.floor:
        floor = _floor_label(stats.floor)
        lines.append(
            f"- [{building} {floor}] {stats.same_floor}건 — 위 {stats.total}건에 포함된 수치임"
        )
    if stats.risk_distribution:
        spread = ", ".join(
            f"{level} {count}건" for level, count in stats.risk_distribution.items() if count
        )
        if spread:
            lines.append(f"- [{building} 전체 {stats.total}건의 위험 등급 분포] {spread}")
    if stats.first_occurred_at:
        lines.append(f"- [{building} 전체] 기간 내 최초 발생: {stats.first_occurred_at}")
    return "\n".join(lines)


def fetch_cases(
    *,
    session_id: str,
    location_text: str,
    detected_hazard: str,
    photo_analysis: str,
    risk: str,
    top_k: int | None = None,
    report_id: str | None = None,
    building_id: int | None = None,
    building_name: str | None = None,
    floor: str | None = None,
) -> CaseBundle:
    """C-5 를 호출해 프롬프트에 넣을 과거사례 텍스트와 집계를 만든다.

    ``building_id`` 가 없으면(위치 미상) 집계는 건너뛰고 목록만 받는다 — ④ 가 그렇다.
    """
    settings = get_settings()
    query_text = join_query(detected_hazard, photo_analysis, risk, location_text)
    try:
        data = get_backend().search_cases(
            session_id,
            query_text,
            top_k or DEFAULT_TOP_K,
            report_id=report_id,
            building_id=building_id,
            building_name=building_name,
            floor=floor,
            window_days=settings.case_window_days,
        )
    except Exception as exc:  # noqa: BLE001 - 부가 정보라 실패해도 계속 간다
        log.warning("cases/search 실패 — 참고 없이 진행한다: %s", exc)
        return CaseBundle(cases_text=FAILED_TEXT, stats_text=STATS_FAILED_TEXT)

    cases = data.get("cases") or []
    blocks = [_format_case(case) for case in cases]
    ids = [str(case.get("report_id") or "") for case in cases]

    raw_stats = data.get("stats")
    stats = CaseStats.model_validate(raw_stats) if raw_stats else None
    if stats is None:
        stats_text = NO_STATS_TEXT if building_id is None and building_name is None else STATS_FAILED_TEXT
    else:
        stats_text = _format_stats(stats)

    return CaseBundle(
        cases_text="\n".join(blocks) if blocks else EMPTY_TEXT,
        stats_text=stats_text,
        case_count=len(cases),
        case_ids=ids,
        stats=stats,
    )
