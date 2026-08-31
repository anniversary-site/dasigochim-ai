"""로컬 실험용 백엔드 대역 (BACKEND_MODE=stub).

Spring 쪽 C-1/C-2/C-5 가 아직 없어도 end-to-end 를 돌릴 수 있게 한다.

- C-1 부서 검색은 임베딩 대신 문자 bigram 자카드 유사도로 근사한다 — 순위가
  그럴듯하기만 하면 파이프라인 검증에는 충분하고, 결정론적이라 재현된다.
- C-5 과거사례는 **백엔드 SQL 의 참조 구현**이다. 정렬(동일 건물 우선 → 최근순)과
  집계 규칙을 백엔드와 같게 유지해야 계약 해석이 갈리지 않는다.
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.backends.base import BackendError
from app.config import get_settings

log = logging.getLogger(__name__)

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
UNRESOLVED_STATUSES = {"RECEIVED", "REVIEWING"}


@lru_cache
def _load(name: str) -> dict[str, Any]:
    path = FIXTURE_DIR / name
    if not path.is_file():
        raise BackendError(f"픽스처가 없다: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _bigrams(text: str) -> set[str]:
    cleaned = "".join(ch for ch in (text or "").lower() if ch.isalnum())
    if len(cleaned) < 2:
        return {cleaned} if cleaned else set()
    return {cleaned[i : i + 2] for i in range(len(cleaned) - 1)}


def _similarity(query: str, document: str) -> float:
    q, d = _bigrams(query), _bigrams(document)
    if not q or not d:
        return 0.0
    return len(q & d) / len(q | d)


class StubBackendClient:
    """부서 업무범위·과거사례를 로컬 픽스처에서 검색한다."""

    def duty_search(self, session_id: str, query_text: str, top_k: int) -> dict[str, Any]:
        data = _load("departments.json")
        scored: list[dict[str, Any]] = []
        for dept in data["departments"]:
            if not dept.get("active", True):
                continue
            for chunk in dept["chunks"]:
                scored.append(
                    {
                        "code": dept["code"],
                        "name": dept["name"],
                        "duty_text": chunk,
                        "score": round(_similarity(query_text, chunk), 4),
                    }
                )
        scored.sort(key=lambda c: c["score"], reverse=True)
        log.info("stub duty_search session=%s top_k=%d hits=%d", session_id, top_k, len(scored))
        return {"candidates": scored[:top_k], "corpus_version": data.get("corpus_version", 1)}

    def resolve_department(
        self, session_id: str, department_code: str, confidence: float, rationale: str
    ) -> dict[str, Any]:
        """코드 유효성 검증 + 임계값 fallback. 실제 백엔드 C-2 의 결정론 로직."""
        settings = get_settings()
        departments = {d["code"]: d for d in _load("departments.json")["departments"]}

        code = (department_code or "").strip()
        picked = departments.get(code)
        valid = picked is not None and picked.get("active", True)

        if valid and float(confidence or 0.0) >= settings.department_confidence_threshold:
            return {
                "department": {
                    "code": picked["code"],
                    "name": picked["name"],
                    "contact": picked.get("contact", ""),
                },
                "assigned_by": "agent",
                "confidence": float(confidence),
                "rationale": rationale or "",
            }

        fallback = departments.get(settings.default_department_code)
        if fallback is None:
            raise BackendError(
                f"기본 부서 코드가 픽스처에 없다: {settings.default_department_code}"
            )
        reason = "유효하지 않은 부서 코드" if not valid else "판정 신뢰도 임계값 미달"
        log.info("stub resolve_department fallback session=%s reason=%s", session_id, reason)
        return {
            "department": {
                "code": fallback["code"],
                "name": fallback["name"],
                "contact": fallback.get("contact", ""),
            },
            "assigned_by": "fallback",
            "confidence": None,
            "rationale": f"{reason}으로 기본 부서에 배정함. (모델 판정: {rationale or '없음'})",
        }

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
        """C-5 대역. **백엔드 SQL 의 참조 구현이다** — 정렬과 집계 규칙이 같아야 한다.

        1단계라 유사도는 쓰지 않는다. 목록은 '동일 건물 먼저, 그다음 최근순' 이고
        ``score`` 는 null 이다 (2단계에서 벡터 거리로 채워진다).
        집계는 건물명이 주어졌을 때만 낸다.
        """
        cases = [_expand_case(case) for case in _load("cases.json")["cases"]]
        cases = [c for c in cases if c["report_id"] != report_id]

        # 목록: 동일 건물 우선, 그다음 최근순. 백엔드의 ORDER BY 와 같은 순서다.
        ranked = sorted(
            cases,
            key=lambda c: (0 if building_name and c["building_name"] == building_name else 1,
                           c["days_ago"]),
        )
        picked = [
            {
                "report_id": c["report_id"],
                "occurred_at": c["occurred_at"],
                "score": None,
                "status": c["status"],
                "building_name": c["building_name"],
                "floor": c["floor"],
                "hazard": c["hazard"],
                "action_fundamental": c["action_fundamental"],
            }
            for c in ranked[:top_k]
        ]

        stats = _aggregate(cases, building_name, floor, window_days) if building_name else None
        log.info(
            "stub search_cases session=%s building=%s window=%dd hits=%d total=%s",
            session_id, building_name, window_days, len(picked),
            stats["total"] if stats else "-",
        )
        return {"cases": picked, "stats": stats}


def _expand_case(case: dict[str, Any]) -> dict[str, Any]:
    """days_ago 를 오늘 기준 날짜로 편다.

    절대 날짜를 픽스처에 박으면 집계 기간이 지나는 순간 '최근 N일 K건' 이 0 이 된다.
    report_id 도 날짜에서 파생시켜 occurred_at 과 어긋나지 않게 한다.
    """
    occurred = date.today() - timedelta(days=int(case["days_ago"]))
    return {
        **case,
        "occurred_at": occurred.isoformat(),
        "report_id": f"R-{occurred.year}-{occurred.month:02d}{occurred.day:02d}",
    }


def _aggregate(
    cases: list[dict[str, Any]], building_name: str, floor: str | None, window_days: int
) -> dict[str, Any]:
    """동일 건물·기간 집계. 백엔드는 이걸 단일 SELECT 로 낸다.

    이번 신고는 이미 목록에서 빠져 있으므로 total 은 '과거 신고 건수' 다.
    """
    window = [
        c for c in cases
        if c["building_name"] == building_name and c["days_ago"] <= window_days
    ]
    risk_distribution: dict[str, int] = {}
    for case in window:
        level = str(case.get("risk_level") or "")
        if level:
            risk_distribution[level] = risk_distribution.get(level, 0) + 1

    return {
        "window_days": window_days,
        "building_name": building_name,
        "floor": floor,
        "total": len(window),
        "unresolved": sum(1 for c in window if c["status"] in UNRESOLVED_STATUSES),
        "same_floor": sum(1 for c in window if floor and c["floor"] == floor),
        "risk_distribution": risk_distribution,
        "first_occurred_at": min((c["occurred_at"] for c in window), default=None),
    }
