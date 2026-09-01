"""문장 임베딩 — Gemini 임베딩 API, 그리고 그것이 없을 때의 대체물.

## 대체물을 두는 이유

문체 검색에서 **주 신호는 역할(role)이지 의미 유사도가 아니다.** '조치 문장은 조치
문장답게' 가 먼저고, '이번 건과 비슷한 상황의 조치' 는 그 다음이다. 그래서 임베딩이
없어도 검색은 성립한다 — 순위만 흐려질 뿐이다. 이 성질 덕분에 대체 경로를 둘 수 있다.

- ``gemini``  : 실제 의미 유사도. 색인·조회 모두 API 를 한 번씩 쓴다.
- ``hash``    : 문자 3-gram 해싱. API 도 numpy 도 없이 돈다. 표기가 겹치는 문장을
                끌어올리는 정도의 성능이지만, 키 없이 색인을 만들고 CI 를 돌릴 수 있다.

색인 파일에는 어느 쪽으로 만들었는지가 적힌다. 조회 시점의 방식이 다르면 벡터를 섞어
비교하는 대신 **역할 순서만으로** 뽑는다 (``retriever`` 참고). 조용히 엉뚱한 순위를
내놓는 것보다 순위를 포기하는 편이 낫다.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
import time
from typing import Any, Sequence

from app.config import get_settings

log = logging.getLogger(__name__)

HASH_EMBEDDER = "hash"
GEMINI_EMBEDDER = "gemini"

# 한 번에 보내는 문장 수. 임베딩 API 는 요청당 개수 제한이 있고, 색인은 300여 조각이라
# 32 면 열 번 남짓이면 끝난다.
_BATCH = 32
_BATCH_PAUSE_SECONDS = 1.0
_RETRY_BASE_SECONDS = 8.0
# 색인 빌드는 기다려도 되고, ④ 조회는 기다리면 안 된다. 같은 함수를 예산으로 가른다.
INDEX_RETRIES = 5
QUERY_RETRIES = 0
_TRANSIENT_MARKERS = ("429", "500", "502", "503", "504", "RESOURCE_EXHAUSTED", "UNAVAILABLE")
_TOKEN = re.compile(r"[0-9A-Za-z가-힣]+")


class EmbeddingError(RuntimeError):
    """임베딩 생성 실패. 색인 빌드에서는 치명적, 조회에서는 폴백 신호다."""


def embedder_id(kind: str) -> str:
    """색인에 적어 두는 방식 식별자. 조회 시점과 다르면 벡터 비교를 포기한다."""
    settings = get_settings()
    if kind == GEMINI_EMBEDDER:
        return f"gemini:{settings.embedding_model}:{settings.embedding_dimensions}"
    return f"hash:{settings.embedding_dimensions}"


# ── 해시 임베딩 ─────────────────────────────────────────────────────────

def hash_embed(text: str, dimensions: int) -> list[float]:
    """문자 3-gram 을 해싱해 고정 차원 벡터로 만든다. 결정적이고 의존성이 없다."""
    vector = [0.0] * dimensions
    normalized = " ".join(_TOKEN.findall(text.lower()))
    for size in (2, 3):
        for i in range(max(0, len(normalized) - size + 1)):
            gram = normalized[i : i + size]
            if gram.isspace():
                continue
            digest = hashlib.blake2b(gram.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "big") % dimensions
            # 부호까지 해시에서 뽑는다. 서로 다른 gram 이 같은 칸에 겹칠 때
            # 항상 더하기만 하면 흔한 gram 쪽으로 벡터가 쏠린다.
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[index] += sign
    return _normalize(vector)


# ── Gemini 임베딩 ───────────────────────────────────────────────────────

def _gemini_embed(texts: Sequence[str], task_type: str, max_retries: int) -> list[list[float]]:
    from google.genai import types

    from app.llm.gemini import _get_client

    settings = get_settings()
    client = _get_client()
    dimensions = settings.embedding_dimensions

    config = types.EmbedContentConfig(
        task_type=task_type, output_dimensionality=dimensions
    )
    out: list[list[float]] = []
    for start in range(0, len(texts), _BATCH):
        batch = list(texts[start : start + _BATCH])
        response = _embed_with_retry(client, settings.embedding_model, batch, config, max_retries)
        got = list(response.embeddings or [])
        if len(got) != len(batch):
            raise EmbeddingError(f"임베딩 개수가 안 맞는다: 요청 {len(batch)} / 응답 {len(got)}")
        # 차원을 줄여 받으면 단위 벡터가 아니게 된다. 코사인만 쓸 거라 여기서 맞춰 둔다.
        out.extend(_normalize(list(e.values or [])) for e in got)
        if start + _BATCH < len(texts):
            time.sleep(_BATCH_PAUSE_SECONDS)
    return out


def _embed_with_retry(
    client: Any, model: str, batch: list[str], config: Any, max_retries: int
) -> Any:
    """임베딩 API 는 분당 요청 수 할당이 좁다. 색인 빌드는 한 번에 열 번 넘게 부른다.

    **재시도 예산은 부르는 쪽이 정한다.** 색인 빌드(``INDEX_RETRIES``)는 사람이 지켜보는
    배치 작업이라 몇십 초 기다려도 되지만 절반만 만들어진 색인은 곤란하다. 반대로 ④ 의
    조회(``QUERY_RETRIES``)는 사용자가 초안을 기다리는 중이고, 실패해도 역할 순서로
    뽑으면 그만이다 — 여기서 24초를 끄는 것이 가장 나쁜 선택이다.
    """
    delay = _RETRY_BASE_SECONDS
    for attempt in range(max_retries + 1):
        try:
            return client.models.embed_content(model=model, contents=batch, config=config)
        except Exception as exc:  # noqa: BLE001 - 재시도 여부를 보고 되던진다
            throttled = any(marker in str(exc) for marker in _TRANSIENT_MARKERS)
            if not throttled or attempt == max_retries:
                raise
            log.warning("임베딩 쿼터/일시 오류 — %.0f초 후 재시도 (%d/%d)", delay, attempt + 1, max_retries)
            time.sleep(delay)
            delay *= 2
    raise EmbeddingError("도달 불가")  # pragma: no cover


# ── 공통 진입점 ─────────────────────────────────────────────────────────

def embed(
    texts: Sequence[str], *, kind: str, task_type: str, max_retries: int = INDEX_RETRIES
) -> list[list[float]]:
    """``kind`` 가 정한 방식으로 임베딩한다. 실패는 그대로 올린다 — 호출부가 정한다."""
    if not texts:
        return []
    if kind == GEMINI_EMBEDDER:
        try:
            return _gemini_embed(texts, task_type, max_retries)
        except Exception as exc:  # noqa: BLE001 - 원인을 한 겹으로 감싸 올린다
            raise EmbeddingError(f"Gemini 임베딩 실패: {exc}") from exc
    dimensions = get_settings().embedding_dimensions
    return [hash_embed(t, dimensions) for t in texts]


def embed_documents(texts: Sequence[str], *, kind: str) -> list[list[float]]:
    """색인 빌드용. 쿼터에 걸리면 기다렸다 다시 부른다."""
    return embed(texts, kind=kind, task_type="RETRIEVAL_DOCUMENT", max_retries=INDEX_RETRIES)


def embed_query(text: str, *, kind: str) -> list[float]:
    """조회용. **기다리지 않는다** — 실패하면 호출부가 역할 순서로 넘어간다."""
    return embed([text], kind=kind, task_type="RETRIEVAL_QUERY", max_retries=QUERY_RETRIES)[0]


# ── 벡터 연산 (numpy 없이) ──────────────────────────────────────────────

def _normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vector))
    if norm <= 0:
        return vector
    return [v / norm for v in vector]


# 파이썬 3.12 의 C 구현 내적. 200개 × 768차원 한 판이 30ms → 7ms 로 준다.
_dot = getattr(math, "sumprod", None)


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """둘 다 단위 벡터라 내적이 곧 코사인이다."""
    if _dot is not None and len(a) == len(b):
        return _dot(a, b)
    return sum(x * y for x, y in zip(a, b))
