r"""재해조사의견서 PDF → 문체 색인.

``ai-server/docs/*.pdf`` 를 파싱해 역할을 붙이고, 비식별 처리한 뒤 임베딩해서
``app/rag/index/style_index.json`` 으로 굳힌다. ④ 신고서 초안이 이 색인을 읽는다.

    # 기본 (Gemini 임베딩. GEMINI_API_KEY 필요)
    .\.venv\Scripts\python.exe scripts\build_rag_index.py

    # 키 없이 (문자 3-gram 해싱. CI·오프라인용)
    .\.venv\Scripts\python.exe scripts\build_rag_index.py --embedder hash

    # 무엇이 색인될지만 눈으로 확인 (파일을 쓰지 않는다)
    .\.venv\Scripts\python.exe scripts\build_rag_index.py --dry-run
    .\.venv\Scripts\python.exe scripts\build_rag_index.py --dry-run --role action

색인 파일은 git 에 넣는다. 받아 온 사람이 PDF 를 다시 파싱하지 않아도 같은 견본을 보게
하려는 것이다 — 프롬프트가 환경마다 달라지는 쪽이 더 나쁘다.

**PDF 를 갈아 끼웠으면 이 스크립트를 다시 돌린다.** 색인은 자동으로 갱신되지 않는다.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.rag import embeddings, extract, profile, store  # noqa: E402
from app.rag.retriever import index_path  # noqa: E402


def docs_dir() -> Path:
    settings = get_settings()
    if settings.rag_docs_dir:
        return Path(settings.rag_docs_dir)
    return Path(__file__).resolve().parent.parent / "docs"


def main() -> int:
    parser = argparse.ArgumentParser(description="재해조사의견서 PDF 로 문체 색인을 만든다.")
    parser.add_argument(
        "--embedder",
        choices=[embeddings.GEMINI_EMBEDDER, embeddings.HASH_EMBEDDER],
        default=embeddings.GEMINI_EMBEDDER,
        help="임베딩 방식 (기본 gemini. 키가 없으면 hash 로 떨어진다)",
    )
    parser.add_argument("--docs", help="PDF 폴더 (비우면 ai-server/docs)")
    parser.add_argument("--out", help="색인 저장 경로 (비우면 app/rag/index/style_index.json)")
    parser.add_argument("--role", help="--dry-run 에서 이 역할만 출력")
    parser.add_argument("--dry-run", action="store_true", help="파일을 쓰지 않고 조각만 출력")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)-5s %(message)s")

    source = Path(args.docs) if args.docs else docs_dir()
    if not source.is_dir():
        print(f"!! PDF 폴더가 없다: {source}")
        return 1

    chunks = extract.parse_directory(source)
    if not chunks:
        print(f"!! 색인할 조각이 없다: {source}")
        return 1

    grouped = extract.by_role(chunks)
    stats = profile.build(chunks)

    print(f"\n문서 {stats.doc_count}건 → 조각 {len(chunks)}개")
    for role, label in extract.ROLE_LABELS.items():
        print(f"  {role:12s} {len(grouped.get(role, [])):4d}  {label}")
    print("\n[문체 규격]")
    print(stats.render())

    if args.dry_run:
        _dump(grouped, args.role)
        print("\n--dry-run 이라 색인을 쓰지 않았다.")
        return 0

    kind = args.embedder
    if kind == embeddings.GEMINI_EMBEDDER and not get_settings().gemini_api_key:
        print("\n!! GEMINI_API_KEY 가 없다 — hash 임베딩으로 만든다.")
        kind = embeddings.HASH_EMBEDDER

    texts = [c.text for c in chunks]
    print(f"\n임베딩 {kind} … 조각 {len(chunks)}개")
    try:
        vectors = embeddings.embed_documents(texts, kind=kind)
    except embeddings.EmbeddingError as exc:
        print(f"!! {exc}\n   --embedder hash 로 다시 시도할 수 있다.")
        return 1

    # 해시 벡터는 방식과 무관하게 항상 굽는다. 운영 중 임베딩 API 가 막혔을 때
    # ④ 가 순위를 통째로 잃는 대신 이쪽으로 갈아탄다 (retriever._query_axis).
    hash_vectors = (
        vectors
        if kind == embeddings.HASH_EMBEDDER
        else embeddings.embed_documents(texts, kind=embeddings.HASH_EMBEDDER)
    )

    index = store.build(
        chunks, vectors, hash_vectors, embedder=embeddings.embedder_id(kind), profile=stats
    )
    out = Path(args.out) if args.out else index_path()
    store.save(index, out)
    print(f"\n완료 → {out}  ({out.stat().st_size / 1024:.0f} KB)")
    return 0


def _dump(grouped: dict[str, list[extract.Chunk]], only: str | None) -> None:
    for role, items in grouped.items():
        if only and role != only:
            continue
        print(f"\n{'=' * 70}\n{role} · {extract.ROLE_LABELS.get(role, '')}  ({len(items)}개)")
        for chunk in items:
            print(f"\n[{chunk.chunk_id}] {chunk.section}")
            print(f"  · {chunk.headline}")
            for body in chunk.body:
                print(f"    {body}")


if __name__ == "__main__":
    raise SystemExit(main())
