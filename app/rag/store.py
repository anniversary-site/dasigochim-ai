"""문체 색인 파일 — 읽기·쓰기·검색.

색인은 JSON 파일 하나다. 벡터 DB 를 붙이지 않은 것은 규모 때문이다: 조각 300여 개,
쿼리당 전수 비교가 밀리초 단위로 끝난다. 여기서 서버를 하나 더 띄우면 얻는 것 없이
"시연 중에 죽을 수 있는 것" 만 하나 늘어난다.

**색인은 빌드 산출물이지만 git 에 넣는다.** 그래야 키 없이 받아 온 사람도, CI 도,
컨테이너도 PDF 를 다시 파싱하지 않고 같은 견본을 본다 — 프롬프트가 환경마다 달라지는
일을 막는 게 우선이다. 다시 만들려면 ``scripts/build_rag_index.py``.
"""

from __future__ import annotations

import json
import logging
from array import array
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from app.rag import embeddings
from app.rag.extract import Chunk
from app.rag.profile import StyleProfile

log = logging.getLogger(__name__)

# 벡터를 소수점 6자리로 자른다. 코사인 순위는 그대로면서 파일이 절반으로 준다.
_PRECISION = 6


def _vec(values: Any = ()) -> "array[float]":
    # 파이썬 list[float] 로 들고 있으면 295×768 벡터가 22MB 를 먹는다 (실측).
    # 컨테이너 예산이 160MB 라 그 자리에서 실격이다. array('f') 는 같은 값을
    # 0.9MB 로 들고, 코사인은 zip 으로 도는 터라 계산 코드는 그대로다.
    return array("f", values or ())


@dataclass(frozen=True)
class Entry:
    chunk: Chunk
    vector: Sequence[float] = field(default_factory=_vec)
    # 같은 조각의 해시 임베딩. 조회 시점에 임베딩 API 가 막히면(쿼터·네트워크) 이쪽으로
    # 갈아탄다 — 순위를 통째로 포기하는 것보다 낫다. 색인이 커지는 값(+0.9MB)만큼은 한다.
    hash_vector: Sequence[float] = field(default_factory=_vec)


@dataclass(frozen=True)
class StyleIndex:
    embedder: str
    built_at: str
    entries: list[Entry]
    profile: StyleProfile
    source_docs: list[str] = field(default_factory=list)

    @property
    def size(self) -> int:
        return len(self.entries)

    @property
    def has_vectors(self) -> bool:
        return any(e.vector for e in self.entries)

    @property
    def has_hash_vectors(self) -> bool:
        return any(e.hash_vector for e in self.entries)

    def role_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for entry in self.entries:
            counts[entry.chunk.role] = counts.get(entry.chunk.role, 0) + 1
        return counts

    def by_role(self, role: str) -> list[Entry]:
        return [e for e in self.entries if e.chunk.role == role]


def build(
    chunks: list[Chunk],
    vectors: list[list[float]],
    hash_vectors: list[list[float]],
    *,
    embedder: str,
    profile: StyleProfile,
) -> StyleIndex:
    for name, vecs in (("벡터", vectors), ("해시 벡터", hash_vectors)):
        if vecs and len(vecs) != len(chunks):
            raise ValueError(f"조각 {len(chunks)}개에 {name} {len(vecs)}개 — 짝이 안 맞는다.")
    entries = [
        Entry(
            chunk=chunk,
            vector=_vec(vectors[i] if vectors else ()),
            hash_vector=_vec(hash_vectors[i] if hash_vectors else ()),
        )
        for i, chunk in enumerate(chunks)
    ]
    return StyleIndex(
        embedder=embedder,
        built_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        entries=entries,
        profile=profile,
        source_docs=sorted({c.doc_id for c in chunks}),
    )


def save(index: StyleIndex, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "embedder": index.embedder,
        "built_at": index.built_at,
        "source_docs": index.source_docs,
        "profile": index.profile.to_dict(),
        "chunks": [
            {
                **entry.chunk.to_dict(),
                "vector": [round(v, _PRECISION) for v in entry.vector],
                "hash_vector": [round(v, _PRECISION) for v in entry.hash_vector],
            }
            for entry in index.entries
        ],
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    log.info("문체 색인 저장 %s (조각 %d, %s)", path, index.size, index.embedder)


def load(path: Path) -> StyleIndex | None:
    """없거나 깨졌으면 None. 색인은 부가 기능이라 여기서 서버를 죽이지 않는다."""
    if not path.is_file():
        log.info("문체 색인이 없다 (%s) — 문체 참조 없이 작성한다", path)
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        entries = [
            Entry(
                chunk=Chunk.from_dict(item),
                vector=_vec(item.get("vector")),
                hash_vector=_vec(item.get("hash_vector")),
            )
            for item in data.get("chunks", [])
        ]
        return StyleIndex(
            embedder=str(data.get("embedder", "")),
            built_at=str(data.get("built_at", "")),
            entries=entries,
            profile=StyleProfile.from_dict(data.get("profile") or {}),
            source_docs=[str(x) for x in data.get("source_docs", [])],
        )
    except Exception as exc:  # noqa: BLE001 - 깨진 색인이 판독을 막지 않는다
        log.warning("문체 색인을 읽지 못했다 (%s): %s", path, exc)
        return None


def search(
    index: StyleIndex,
    query_vector: Sequence[float],
    role: str,
    k: int,
    *,
    field_name: str = "vector",
) -> list[Entry]:
    """역할 안에서 코사인 상위 k 개.

    ``field_name`` 은 어느 벡터로 재느냐다 (``vector`` = 색인이 만들어진 방식,
    ``hash_vector`` = 조회 임베딩이 막혔을 때의 대체 축). 벡터가 없으면 원문 순서로 k 개.
    """
    pool = index.by_role(role)
    if not pool:
        return []
    if not query_vector:
        return pool[:k]
    scored = [
        (embeddings.cosine(query_vector, vector), i, entry)
        for i, entry in enumerate(pool)
        if (vector := getattr(entry, field_name, ()))
    ]
    if not scored:
        return pool[:k]
    # 점수가 같을 때 순서가 흔들리지 않도록 원래 순번을 두 번째 키로 쓴다.
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [entry for _, _, entry in scored[:k]]
