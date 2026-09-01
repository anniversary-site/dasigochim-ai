"""Langfuse 로 나가는 값에서 개인정보를 지운다.

신고 본문은 제보자가 자유롭게 쓴 글이라 연락처나 이메일이 섞일 수 있고,
그것이 그대로 외부 SaaS 로 나가면 안 된다.

## 무엇을 지우고 무엇을 남기는가

지운다 — **구조가 뚜렷해 정규식으로 확실히 잡히는 식별자**만.

    이메일 · 휴대폰번호 · 유선번호 · 주민등록번호 형태 · 카드번호 형태

남긴다 — **위치와 위험 요소 서술**. 판독 품질을 보려고 트레이스를 남기는 것인데
"하비에르관 2층 정수기 앞" 을 지우면 트레이스가 쓸모없어진다. 건물·층은 그 자체로
개인을 지목하지 않는다.

## 한계 (분명히 알고 쓸 것)

정규식은 **구조가 있는 식별자만** 잡는다. 제보자가 본문에 사람 이름을 적으면
("김OO 교수님 연구실 앞") 그건 걸러지지 않는다. 이름까지 지우려면 NER 이 필요하고,
그건 LLM 호출을 하나 더 만드는 일이라 여기서는 하지 않는다.
**신고 본문에 이름을 적지 않도록 안내하는 것이 더 확실한 방어다.**

## 어느 API 를 썼나

Langfuse 4.x 는 새 설정에 ``mask_otel_spans`` 를 권한다. 그쪽은 raw OTel 스팬 속성을
다루며 서드파티 계측(OpenAI SDK 래퍼 등)까지 덮는 것이 목적이다.
이 서버는 Gemini 를 직접 부르고 트레이스 값을 전부 우리 손으로
``start_as_current_observation`` / ``update`` 에 넣으므로, 그 경로만 덮는 레거시
``mask`` 로 충분하고 훨씬 단순하다. 서드파티 계측을 붙이게 되면 그때 옮긴다.
"""

from __future__ import annotations

import re
from typing import Any

REDACTED = "[제거됨]"

# 순서가 의미 있다. 넓은 패턴이 좁은 패턴을 삼키지 않도록 좁은 것부터 둔다.
PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("email", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    # 주민등록번호: 6자리-7자리. 뒷자리 첫 글자는 1~4(내국인)·5~8(외국인)
    ("rrn", re.compile(r"\b\d{6}\s*[-–]\s*[1-8]\d{6}\b")),
    ("card", re.compile(r"\b(?:\d{4}[ -]){3}\d{4}\b")),
    # 휴대폰: 010-1234-5678 / 01012345678 / 010 1234 5678
    ("mobile", re.compile(r"\b01[016789][ -]?\d{3,4}[ -]?\d{4}\b")),
    # 유선: 02-705-8114 등. 부서 대표번호도 함께 지워지지만 트레이스에서 굳이 필요 없다.
    ("landline", re.compile(r"\b0(?:2|[3-6][1-5])[ -]?\d{3,4}[ -]?\d{4}\b")),
)

MAX_DEPTH = 6


def scrub(text: str) -> str:
    for _, pattern in PATTERNS:
        text = pattern.sub(REDACTED, text)
    return text


def _walk(value: Any, depth: int) -> Any:
    if depth > MAX_DEPTH:
        return value
    if isinstance(value, str):
        return scrub(value)
    if isinstance(value, dict):
        return {k: _walk(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        walked = [_walk(v, depth + 1) for v in value]
        return type(value)(walked) if isinstance(value, tuple) else walked
    return value


def mask(*, data: Any, **_: Any) -> Any:
    """Langfuse ``mask`` 콜백.

    **어떤 경우에도 예외를 밖으로 내지 않는다.** 마스킹이 터져서 판독이 멈추면
    본말이 전도된다. 실패하면 그 값을 통째로 가려 내보낸다 — 원본을 흘리는 것보다
    가리는 쪽이 안전하다.
    """
    try:
        return _walk(data, 0)
    except Exception:  # noqa: BLE001 - 마스킹 실패가 추적·판독을 막지 않는다
        return REDACTED
