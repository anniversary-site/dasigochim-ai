"""로컬 실험용 백엔드 대역 (BACKEND_MODE=stub).

Spring 쪽 C-1/C-2/C-5 가 아직 없어도 end-to-end 를 돌릴 수 있게 한다.
검색은 임베딩 대신 문자 bigram 자카드 유사도로 근사한다 — 순위가
그럴듯하기만 하면 파이프라인 검증에는 충분하고, 결정론적이라 재현된다.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.backends.base import BackendError
from app.config import get_settings

log = logging.getLogger(__name__)

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"


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

    def search_cases(self, session_id: str, query_text: str, top_k: int) -> dict[str, Any]:
        cases = _load("cases.json")["cases"]
        scored = []
        for case in cases:
            document = " ".join(
                str(case.get(k, "")) for k in ("hazard", "photo_analysis", "risk")
            )
            score = _similarity(query_text, document)
            if score <= 0.0:
                continue
            scored.append({**case, "score": round(score, 4)})
        scored.sort(key=lambda c: c["score"], reverse=True)
        log.info("stub search_cases session=%s top_k=%d hits=%d", session_id, top_k, len(scored))
        return {"cases": scored[:top_k]}
