"""문체 통계 — 코퍼스에서 '규격' 을 뽑는다.

검색으로 붙이는 견본은 이번 건과 가까운 몇 개뿐이라 코퍼스 전체의 습관은 보여 주지
못한다. 반대로 통계는 전체를 요약하지만 문장 하나를 보여 주지는 못한다. 둘 다 필요하다.

    통계(여기)  →  "종결은 ~함/~있음/~됨 이 8할, 추정은 반드시 ~것으로 추정됨"
    견본(retriever) → 그 규칙이 실제 문장에서 어떻게 생겼는지

통계는 색인을 만들 때 한 번 계산해 ``style_profile.json`` 으로 굳힌다. 요청마다 세면
같은 값을 반복해 계산하는 데다, 무엇보다 프롬프트가 조용히 흔들린다.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable

from app.rag.extract import ENDING_RE, Chunk, sentences

# 확인된 사실과 추정을 어미로 가르는 습관. 이 코퍼스의 가장 옮길 만한 버릇이다.
_HEDGE_RE = re.compile(r"추정|판단됨|보임|가능성|예상|것으로\s*보")


@dataclass(frozen=True)
class StyleProfile:
    doc_count: int = 0
    chunk_count: int = 0
    sentence_count: int = 0
    avg_sentence_chars: int = 0
    ending_counts: list[tuple[str, int]] = field(default_factory=list)
    hedge_ratio: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "doc_count": self.doc_count,
            "chunk_count": self.chunk_count,
            "sentence_count": self.sentence_count,
            "avg_sentence_chars": self.avg_sentence_chars,
            "ending_counts": [list(x) for x in self.ending_counts],
            "hedge_ratio": round(self.hedge_ratio, 4),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StyleProfile":
        return cls(
            doc_count=int(data.get("doc_count", 0)),
            chunk_count=int(data.get("chunk_count", 0)),
            sentence_count=int(data.get("sentence_count", 0)),
            avg_sentence_chars=int(data.get("avg_sentence_chars", 0)),
            ending_counts=[(str(a), int(b)) for a, b in data.get("ending_counts", [])],
            hedge_ratio=float(data.get("hedge_ratio", 0.0)),
        )

    @property
    def top_endings(self) -> list[tuple[str, float]]:
        total = sum(c for _, c in self.ending_counts) or 1
        return [(e, c / total) for e, c in self.ending_counts[:8]]

    def render(self) -> str:
        """프롬프트에 넣을 몇 줄. 값이 없으면 빈 문자열 — 없는 통계를 지어내지 않는다."""
        if not self.sentence_count:
            return ""
        endings = ", ".join(f"{e} {pct:.0%}" for e, pct in self.top_endings if pct >= 0.02)
        lines = [
            f"- 표본: 재해조사의견서 {self.doc_count}건 / 문장 {self.sentence_count}개",
            f"- 종결어미 분포: {endings}",
            f"- 문장 길이: 평균 {self.avg_sentence_chars}자 (한 문장 = 사실 하나)",
            f"- 문장의 {self.hedge_ratio:.0%}가 추정 표현(~것으로 추정됨/~가능성이 있음)을 담고 있음"
            " — 확인된 것과 미확인을 어미로 가른다",
        ]
        return "\n".join(lines)


def _ending_of(sentence: str) -> str | None:
    m = ENDING_RE.search(sentence.strip())
    return m.group(1) if m else None


def build(chunks: Iterable[Chunk]) -> StyleProfile:
    chunk_list = list(chunks)
    lines = list(sentences(chunk_list))
    if not lines:
        return StyleProfile()

    endings: Counter[str] = Counter()
    hedged = 0
    for line in lines:
        ending = _ending_of(line)
        if ending:
            endings[ending] += 1
        if _HEDGE_RE.search(line):
            hedged += 1

    return StyleProfile(
        doc_count=len({c.doc_id for c in chunk_list}),
        chunk_count=len(chunk_list),
        sentence_count=len(lines),
        avg_sentence_chars=sum(len(s) for s in lines) // len(lines),
        ending_counts=endings.most_common(),
        hedge_ratio=hedged / len(lines),
    )
