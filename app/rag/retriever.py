"""문체 견본 검색 — 프롬프트에 붙일 한 덩어리를 만든다.

## 무엇을 검색하는가

**역할이 주 신호, 의미 유사도가 보조 신호다.** 신고서의 각 항목은 재해조사의견서의
특정 절과 같은 일을 한다.

    photo_analysis          ↔  4장 조사·확인 내용      (observation)
    hazard / risk           ↔  5장 가. 작업 현장의 위험요인 (hazard)
    risk (원인)              ↔  5장 나. 사고원인          (cause)
    action_urgent/fundamental↔  5장 다. 안전조치 권고      (action)

그래서 항목마다 같은 역할의 문장을 붙인다. 유사도는 그 역할 안에서 "이번 건과 비슷한
상황" 을 앞으로 당길 뿐이라, 임베딩이 없어도 검색은 성립한다.

## 무엇을 검색하지 않는가

내용이다. 이 코퍼스는 산업재해(톤백·지게차·지붕)이고 서비스는 대학 캠퍼스 안전신고라
사실이 겹칠 일이 없다. 그런데도 모델은 눈앞의 문장을 베끼려 들기 때문에, 견본 블록
머리에 경고를 박고(``_WARNING``) 조각 길이를 짧게 잘라 '옮겨 쓸 만한 덩어리' 자체를
남기지 않는다. 프롬프트 쪽 규칙(draft/v3)과 짝으로 움직인다.

## 실패

과거사례(``app/cases.py``)와 같은 태도다 — 문체 참조는 부가 정보이므로 색인이 없든
임베딩이 실패하든 초안 작성을 막지 않고 안내 문구로 우회한다.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from app.config import get_settings
from app.rag import embeddings, store
from app.rag.extract import ROLE_ACTION, ROLE_CAUSE, ROLE_HAZARD, ROLE_OBSERVATION
from app.rag.store import StyleIndex

log = logging.getLogger(__name__)

DISABLED_TEXT = "(문체 참조 사용 안 함 — 아래 [작성 규칙]만 따를 것)"
MISSING_TEXT = "(문체 색인 없음 — 아래 [작성 규칙]만 따를 것)"

# 견본을 붙일 순서. 신고서 항목 이름을 그대로 라벨에 넣어 어디에 쓰는 문체인지 못 헷갈리게 한다.
RETRIEVAL_PLAN: tuple[tuple[str, str], ...] = (
    (ROLE_OBSERVATION, "photo_analysis — 관찰된 상태를 적는 문체"),
    (ROLE_HAZARD, "hazard · risk — 위험 요소를 짚고 위험성으로 잇는 문체"),
    (ROLE_CAUSE, "risk — 원인을 인과로 풀어 쓰는 문체"),
    (ROLE_ACTION, "action_urgent · action_fundamental — 조치를 제안하는 문체"),
)

# 역할마다 필요한 수의 몇 배를 후보로 뽑아 두고 문서를 흩어 고른다.
CANDIDATE_FACTOR = 4

_WARNING = (
    "※ 아래는 산업재해 조사의견서(다른 사건)의 발췌임. **문장 구조·어미·서술 순서만**"
    " 본뜨고, 등장하는 사물·장소·수치·조치 내용은 이번 건과 무관하므로 옮겨 쓰지 않음."
)


@dataclass(frozen=True)
class StyleBundle:
    """프롬프트에 넣을 문체 참조 한 덩어리."""

    text: str
    source: str = "none"           # embedding | hash | role-order | none
    exemplar_ids: list[str] = field(default_factory=list)
    profile_text: str = ""

    @property
    def used(self) -> bool:
        return bool(self.exemplar_ids)


@lru_cache
def index_path() -> Path:
    settings = get_settings()
    if settings.rag_index_path:
        return Path(settings.rag_index_path)
    return Path(__file__).resolve().parent / "index" / "style_index.json"


@lru_cache
def get_index() -> StyleIndex | None:
    """색인은 프로세스당 한 번만 읽는다. 300여 조각이라 통째로 메모리에 둔다."""
    return store.load(index_path())


def reload() -> None:
    """색인을 다시 만든 뒤 캐시를 비운다 (프롬프트 reload 와 같은 용도)."""
    get_index.cache_clear()
    index_path.cache_clear()
    _resume_embedding()


def status() -> dict:
    """점검용 요약. ``GET /v1/rag/status`` 가 그대로 돌려준다."""
    settings = get_settings()
    index = get_index() if settings.rag_enabled else None
    if index is None:
        return {
            "enabled": settings.rag_enabled,
            "indexed": False,
            "index_path": str(index_path()),
            "hint": "scripts/build_rag_index.py 로 색인을 만들 것",
        }
    return {
        "enabled": True,
        "indexed": True,
        "index_path": str(index_path()),
        "embedder": index.embedder,
        "built_at": index.built_at,
        "chunk_count": index.size,
        "role_counts": index.role_counts(),
        "source_docs": index.source_docs,
        "has_vectors": index.has_vectors,
        "has_hash_vectors": index.has_hash_vectors,
        "profile": index.profile.to_dict(),
        "profile_text": index.profile.render(),
    }


# 임베딩 API 가 한 번 막히면(분당 쿼터) 대개 잠시 계속 막힌다. 그동안 요청마다 왕복
# 350ms 를 버리고 같은 경고를 쌓느니, 잠깐 시도 자체를 쉬고 해시 축으로 간다.
EMBED_PAUSE_SECONDS = 60.0
_embed_paused_until = 0.0


def _embedding_paused() -> bool:
    return time.monotonic() < _embed_paused_until


def _pause_embedding() -> None:
    global _embed_paused_until
    _embed_paused_until = time.monotonic() + EMBED_PAUSE_SECONDS


def _resume_embedding() -> None:
    global _embed_paused_until
    _embed_paused_until = 0.0


def _hash_query(index: StyleIndex, query: str) -> tuple[list[float], str, str]:
    """해시 축으로 내려간다. 임베딩 API 가 막혔을 때의 두 번째 선택지."""
    if not index.has_hash_vectors:
        return [], "role-order", "vector"
    dimensions = len(next(e.hash_vector for e in index.entries if e.hash_vector))
    return embeddings.hash_embed(query, dimensions), "hash", "hash_vector"


def _query_axis(index: StyleIndex, query: str) -> tuple[list[float], str, str]:
    """조회 벡터, 검색 방식 이름, 그리고 비교할 벡터 필드.

    세 갈래로 내려간다.

        embedding   색인과 같은 방식으로 질의를 임베딩했다. 가장 좋은 순위.
        hash        임베딩이 막혔다(쿼터·네트워크). 해시 축으로 갈아탄다.
        role-order  둘 다 없다. 순위를 포기하고 원문 순서로 앞에서 k 개.

    방식이 어긋난 채 코사인을 재면 숫자는 나오지만 순위는 뜻이 없다. 그래서 색인에 적힌
    방식과 지금 설정이 다르면 **섞어 재지 않고** 해시 축으로 내려간다.
    """
    if not query.strip():
        return [], "role-order", "vector"
    if not index.has_vectors or _embedding_paused():
        return _hash_query(index, query)

    kind = (
        embeddings.GEMINI_EMBEDDER
        if index.embedder.startswith("gemini:")
        else embeddings.HASH_EMBEDDER
    )
    if embeddings.embedder_id(kind) != index.embedder:
        log.warning(
            "색인(%s)과 현재 설정(%s)의 임베딩 방식이 다르다 — 해시 축으로 검색한다",
            index.embedder, embeddings.embedder_id(kind),
        )
        return _hash_query(index, query)

    try:
        vector = embeddings.embed_query(query, kind=kind)
    except Exception as exc:  # noqa: BLE001 - 순위가 흐려질 뿐 초안은 그대로 쓴다
        _pause_embedding()
        log.warning(
            "문체 검색 임베딩 실패 — %.0f초간 해시 축으로 검색한다: %s",
            EMBED_PAUSE_SECONDS, exc,
        )
        return _hash_query(index, query)
    _resume_embedding()
    return vector, "embedding", "vector"


def _pick(candidates: list[store.Entry], k: int, used_docs: set[str]) -> list[store.Entry]:
    """순위를 지키되 **같은 사건에서 몰아 뽑지 않는다.**

    견본 8개가 한 재해조사서에서 나오면 그 사건의 사물·수치가 프롬프트 안에서 서로를
    보강해 하나의 이야기가 된다. 그럴수록 모델은 그것을 '이번 건의 재료' 로 읽는다.
    문체를 보여 주는 데는 오히려 서로 다른 사건에서 온 문장이 낫다 — 공통점이
    문체밖에 없기 때문이다.
    """
    picked: list[store.Entry] = []
    seen: set[int] = set()
    for pass_no in (1, 2):  # 1: 새 문서만, 2: 자리가 남으면 순위대로 채운다
        for entry in candidates:
            if len(picked) >= k:
                return picked
            if id(entry) in seen:
                continue
            if pass_no == 1 and entry.chunk.doc_id in used_docs:
                continue
            picked.append(entry)
            seen.add(id(entry))
            used_docs.add(entry.chunk.doc_id)
    return picked


def _format_entry(entry: store.Entry, limit: int) -> str:
    chunk = entry.chunk
    lines = [f"  · {chunk.headline}"]
    used = len(chunk.headline)
    for body in chunk.body:
        if used >= limit:
            break
        lines.append(f"    {body[:limit - used]}")
        used += len(body)
    return "\n".join(lines)


def retrieve(query: str, *, top_k: int | None = None) -> StyleBundle:
    """이번 건과 가까운 문체 견본을 역할별로 뽑아 프롬프트 블록으로 만든다."""
    settings = get_settings()
    if not settings.rag_enabled:
        return StyleBundle(text=DISABLED_TEXT)

    try:
        index = get_index()
    except Exception as exc:  # noqa: BLE001
        log.warning("문체 색인 로드 실패 — 문체 참조 없이 진행한다: %s", exc)
        return StyleBundle(text=MISSING_TEXT)

    if index is None or not index.entries:
        return StyleBundle(text=MISSING_TEXT)

    k = top_k or settings.rag_top_k_per_role
    limit = settings.rag_max_chars_per_exemplar
    vector, source, field_name = _query_axis(index, query)

    blocks: list[str] = []
    ids: list[str] = []
    used_docs: set[str] = set()
    for role, label in RETRIEVAL_PLAN:
        # 분산에 쓸 여유분까지 뽑아 두고 _pick 이 고른다.
        pool = store.search(index, vector, role, k * CANDIDATE_FACTOR, field_name=field_name)
        hits = _pick(pool, k, used_docs)
        if not hits:
            continue
        blocks.append(f"〈{label}〉\n" + "\n".join(_format_entry(h, limit) for h in hits))
        ids.extend(h.chunk.chunk_id for h in hits)

    if not blocks:
        return StyleBundle(text=MISSING_TEXT, source=source)

    profile_text = index.profile.render()
    parts = [_WARNING, "", *blocks]
    if profile_text:
        parts = [
            f"[문체 규격 — 재해조사의견서 {index.profile.doc_count}건에서 집계]",
            profile_text,
            "",
            "[문체 견본]",
            *parts,
        ]
    return StyleBundle(
        text="\n".join(parts).strip(),
        source=source,
        exemplar_ids=ids,
        profile_text=profile_text,
    )
