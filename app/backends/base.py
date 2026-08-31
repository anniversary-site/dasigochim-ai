"""백엔드 HTTP 노드(C-1 / C-2 / C-5)의 계약.

Dify HTTP 노드가 하던 일을 그대로 옮긴 것이다. 반환값은 백엔드 응답 JSON
그대로다 — stub 과 http 구현을 바꿔 끼워도 뒤쪽 파싱 코드가 동일하도록.
"""

from __future__ import annotations

from typing import Any, Protocol


class BackendError(RuntimeError):
    """백엔드 호출 실패. 라우터에서 502 로 매핑된다."""

    def __init__(self, message: str, *, status_code: int = 0, body: str = "") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class BackendClient(Protocol):
    """AI 서버가 Spring 백엔드에 거는 내부 호출."""

    def duty_search(self, session_id: str, query_text: str, top_k: int) -> dict[str, Any]:
        """C-1 POST /internal/v1/rag/duty-search

        returns: {"candidates": [{"code","name","duty_text","score"}], "corpus_version": int}
        """

    def resolve_department(
        self, session_id: str, department_code: str, confidence: float, rationale: str
    ) -> dict[str, Any]:
        """C-2 POST /internal/v1/departments/resolve

        returns: {"department": {"code","name","contact"},
                  "assigned_by": "agent"|"fallback",
                  "confidence": float|None, "rationale": str}
        """

    def search_cases(
        self,
        session_id: str,
        query_text: str,
        top_k: int,
        *,
        report_id: str | None = None,
        building_id: int | None = None,
        building_name: str | None = None,
        floor: str | None = None,
        window_days: int = 30,
    ) -> dict[str, Any]:
        """C-5 POST /internal/v1/cases/search

        cases 는 관련 과거 신고 목록, stats 는 동일 건물·기간 집계다.
        stats 는 순수 SQL 집계라 임베딩 없이도 채워지며, cases 의 순위 기준만
        2단계에서 'query_embedding 유사도' 로 바뀐다 (응답 계약은 그대로).

        ``report_id`` 는 이번 신고를 결과에서 빼기 위한 것이고,
        ``building_id`` 가 없으면 집계 대상이 없으므로 stats 는 null 이다.

        returns: {"cases": [{"report_id","occurred_at","score","status",
                             "building_name","floor","hazard","action_fundamental"}],
                  "stats": {"window_days","building_name","floor","total","unresolved",
                            "same_floor","risk_distribution","first_occurred_at"} | None}
        """
