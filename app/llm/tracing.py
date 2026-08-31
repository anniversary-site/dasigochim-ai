"""Langfuse 추적 (generation tracing).

`app/prompts/langfuse_source.py` 가 프롬프트 관리라면, 이쪽은 나머지 절반이다.
**어느 프롬프트 버전이 어떤 출력을 냈는지**가 자동으로 쌓여야 A/B 비교의 근거가 된다.
프롬프트 버전을 되돌릴 수단(`prompt_versions`)은 있어도, 결과가 남지 않으면
비교할 것이 없다.

## 원칙: 텔레메트리는 판독을 막지 않는다

이 모듈의 어떤 함수도 예외를 밖으로 내보내지 않는다. 키가 없거나 Langfuse 가
죽어도 전부 조용히 no-op 으로 떨어지고 호출부는 그대로 진행한다. 경고는 **한 번만**
찍는다 — LLM 호출마다 같은 경고가 쌓이면 진짜 오류가 묻힌다.

## 구조

    request_trace(session_id, "⑤ 요약·인사이트")   ← API 호출 1회 = 트레이스 1개
      └─ generation("1805000005 제목·요약 [summary/v1]")
      └─ generation("1805000006 인사이트 [insight/v1]")

`session_id` 는 트레이스 속성으로 올라가므로 Langfuse **Sessions** 화면에서
①→②×3→③→④→⑤ 가 신고 한 건으로 묶여 보인다.

SDK 는 4.15.1 기준이다. 공식 문서(v3)의 `start_as_current_generation` 은 없고
`start_as_current_observation(as_type="generation")` 이며, 트레이스 속성은
`propagate_attributes()` 로 붙인다 — 설치본을 `inspect` 로 확인한 결과다.
"""

from __future__ import annotations

import logging
from contextlib import ExitStack, contextmanager
from typing import Any, Iterator

from app.config import get_settings

log = logging.getLogger(__name__)

_warned = False


def _warn_once(what: str, exc: Exception) -> None:
    global _warned
    if not _warned:
        _warned = True
        log.warning("langfuse 추적을 끈다 (%s): %s — 판독은 그대로 진행한다", what, exc)


def enabled() -> bool:
    settings = get_settings()
    return settings.langfuse_tracing and settings.langfuse_ready


def _client() -> Any | None:
    if not enabled():
        return None
    try:
        from app.prompts.langfuse_source import get_langfuse

        return get_langfuse()
    except Exception as exc:  # noqa: BLE001 - 추적 실패는 삼킨다
        _warn_once("클라이언트 생성", exc)
        return None


class Generation:
    """생성 1건의 기록 핸들. Langfuse 가 없으면 모든 호출이 no-op 이다."""

    __slots__ = ("_span",)

    def __init__(self, span: Any | None = None) -> None:
        self._span = span

    def finish(
        self,
        *,
        output: Any = None,
        usage: dict[str, int] | None = None,
        metadata: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        if self._span is None:
            return
        payload: dict[str, Any] = {}
        if output is not None:
            payload["output"] = output
        if usage:
            payload["usage_details"] = usage
        if metadata:
            payload["metadata"] = metadata
        if error:
            payload["level"] = "ERROR"
            payload["status_message"] = error[:500]
        if not payload:
            return
        try:
            self._span.update(**payload)
        except Exception as exc:  # noqa: BLE001
            _warn_once("generation 기록", exc)


@contextmanager
def request_trace(session_id: str | None, name: str) -> Iterator[None]:
    """API 호출 1회를 트레이스 1개로 묶는다.

    ④⑤ 는 LLM 을 두 번 부르므로 이 묶음이 없으면 트레이스가 쪼개진다.
    """
    client = _client()
    if client is None:
        yield
        return

    with ExitStack() as stack:
        try:
            from langfuse import propagate_attributes

            # propagate_attributes 는 트레이스 '속성' 만 붙인다. 부모 스팬을 열지 않으면
            # generation 하나하나가 각자 루트가 되어 트레이스가 호출 수만큼 쪼개진다
            # (④⑤ 가 2개로 갈라졌다). 루트 스팬을 하나 열어 그 아래로 모은다.
            stack.enter_context(
                propagate_attributes(session_id=session_id or None, trace_name=name)
            )
            stack.enter_context(
                client.start_as_current_observation(name=name, as_type="span")
            )
        except Exception as exc:  # noqa: BLE001
            _warn_once("트레이스 속성", exc)
        yield


@contextmanager
def generation(
    *,
    name: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    model_parameters: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    langfuse_prompt: Any | None = None,
) -> Iterator[Generation]:
    """LLM 호출 1회를 generation 으로 기록한다.

    ``langfuse_prompt`` 는 Langfuse 에서 받아 온 프롬프트 객체다. 넘기면 UI 에서
    "이 프롬프트 버전의 생성 목록" 으로 묶여 A/B 지표가 붙는다. 파일 모드에서는
    None 이므로 같은 정보를 ``metadata`` 로도 함께 싣는다.
    """
    client = _client()
    if client is None:
        yield Generation()
        return

    with ExitStack() as stack:
        span = None
        try:
            span = stack.enter_context(
                client.start_as_current_observation(
                    name=name,
                    as_type="generation",
                    # 사진 바이트는 싣지 않는다 — 트레이스가 수 MB 로 불어난다.
                    input=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    model=model,
                    model_parameters=model_parameters or None,
                    metadata=metadata or None,
                    prompt=langfuse_prompt,
                )
            )
        except Exception as exc:  # noqa: BLE001
            _warn_once("generation 시작", exc)
            span = None
        yield Generation(span)


def usage_from_gemini(response: Any) -> dict[str, int]:
    """Gemini 응답의 토큰 사용량을 Langfuse usage_details 로 옮긴다.

    필드가 없거나 None 이면 빈 dict 다 — 사용량이 없다고 추적을 실패시키지 않는다.
    """
    meta = getattr(response, "usage_metadata", None)
    if meta is None:
        return {}
    mapping = {
        "input": "prompt_token_count",
        "output": "candidates_token_count",
        "total": "total_token_count",
        "thoughts": "thoughts_token_count",
    }
    usage: dict[str, int] = {}
    for key, attr in mapping.items():
        value = getattr(meta, attr, None)
        if isinstance(value, int) and value > 0:
            usage[key] = value
    return usage


def flush() -> None:
    """종료 시 남은 이벤트를 밀어 넣는다. 실패해도 종료를 막지 않는다."""
    client = _client()
    if client is None:
        return
    try:
        client.flush()
    except Exception as exc:  # noqa: BLE001
        _warn_once("flush", exc)
