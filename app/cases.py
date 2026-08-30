"""C-5 과거사례 검색 + 목록 조립. ④ 와 ⑤ 가 같은 코드를 쓴다.

과거사례는 부가 정보다. 조회에 실패해도 초안 작성을 막지 않고
'(과거사례 조회 실패 — 참고 없이 작성)' 으로 우회한다
(Dify HTTP 노드의 error_strategy: default-value 대응).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.backends import get_backend
from app.common import join_query

log = logging.getLogger(__name__)

DEFAULT_TOP_K = 4
FAILED_TEXT = "(과거사례 조회 실패 — 참고 없이 작성)"
EMPTY_TEXT = "(유사 과거사례 없음)"


@dataclass
class CaseBundle:
    cases_text: str
    case_count: int = 0
    case_ids: list[str] = field(default_factory=list)


def fetch_cases(
    *,
    session_id: str,
    location_text: str,
    detected_hazard: str,
    photo_analysis: str,
    risk: str,
    top_k: int | None = None,
) -> CaseBundle:
    query_text = join_query(detected_hazard, photo_analysis, risk, location_text)
    try:
        data = get_backend().search_cases(session_id, query_text, top_k or DEFAULT_TOP_K)
    except Exception as exc:  # noqa: BLE001 - 부가 정보라 실패해도 계속 간다
        log.warning("cases/search 실패 — 참고 없이 진행한다: %s", exc)
        return CaseBundle(cases_text=FAILED_TEXT)

    cases = data.get("cases") or []
    blocks, ids = [], []
    for c in cases:
        report_id = str(c.get("report_id") or "")
        ids.append(report_id)
        blocks.append(
            "- report_id: %s (%s, 유사도 %.2f)\n  위험 요소: %s\n  당시 근본 조치: %s"
            % (
                report_id,
                c.get("occurred_at") or "일자미상",
                float(c.get("score") or 0.0),
                c.get("hazard") or "",
                c.get("action_fundamental") or "",
            )
        )
    return CaseBundle(
        cases_text="\n".join(blocks) if blocks else EMPTY_TEXT,
        case_count=len(cases),
        case_ids=ids,
    )
