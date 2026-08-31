"""실제 Spring 백엔드 호출 구현 (BACKEND_MODE=http).

Dify HTTP 노드와 동일하게 X-Agent-Token 헤더를 붙이고, 본문은 파이썬에서
json.dumps 로 조립해 보낸다 (제보자 발화에 큰따옴표가 섞여도 안전하도록).
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from app.backends.base import BackendError
from app.config import get_settings

log = logging.getLogger(__name__)


class HttpBackendClient:
    def __init__(self) -> None:
        settings = get_settings()
        self._base_url = settings.backend_base_url.rstrip("/")
        self._token = settings.agent_token
        self._timeout = settings.backend_timeout_seconds

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        body = json.dumps(payload, ensure_ascii=False)
        try:
            res = httpx.post(
                url,
                content=body.encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "X-Agent-Token": self._token,
                },
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise BackendError(f"{path} 호출 실패: {exc}") from exc

        if res.status_code != 200:
            raise BackendError(
                f"{path} 실패: HTTP {res.status_code} / {res.text[:300]}",
                status_code=res.status_code,
                body=res.text,
            )
        try:
            return res.json()
        except ValueError as exc:
            raise BackendError(
                f"{path} 응답 JSON 파싱 실패: {res.text[:300]}",
                status_code=res.status_code,
                body=res.text,
            ) from exc

    def duty_search(self, session_id: str, query_text: str, top_k: int) -> dict[str, Any]:
        return self._post(
            "/internal/v1/rag/duty-search",
            {"session_id": session_id, "query_text": query_text, "top_k": top_k},
        )

    def resolve_department(
        self, session_id: str, department_code: str, confidence: float, rationale: str
    ) -> dict[str, Any]:
        return self._post(
            "/internal/v1/departments/resolve",
            {
                "session_id": session_id,
                "department_code": (department_code or "").strip(),
                "confidence": float(confidence or 0.0),
                "rationale": rationale or "",
            },
        )

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
        """C-5. 값이 없는 위치 필드는 빼고 보낸다 — 백엔드가 '집계 안 함' 으로 읽는다.

        2단계에서 ``query_embedding`` 이 여기 추가된다. 백엔드는 이 키가 없거나
        null 이면 최근순으로, 있으면 벡터 거리순으로 정렬한다.
        """
        payload: dict[str, Any] = {
            "session_id": session_id,
            "query_text": query_text,
            "top_k": top_k,
            "window_days": window_days,
        }
        optional = {
            "report_id": report_id,
            "building_id": building_id,
            "building_name": building_name,
            "floor": floor,
        }
        payload.update({k: v for k, v in optional.items() if v is not None})
        return self._post("/internal/v1/cases/search", payload)
