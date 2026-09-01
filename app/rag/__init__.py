"""문체 RAG — 재해조사의견서에서 '쓰는 법' 만 가져온다.

``ai-server/docs/*.pdf`` 는 고용노동부 재해조사의견서 12건이다. 사고 내용은 산업재해
(톤백·지게차·축사 지붕)라 대학 캠퍼스 안전신고와 겹치지 않는다. 이 문서를 두는 이유는
하나다 — **감독관이 사실을 적는 방식**을 ④ 신고서 초안이 따라 하게 하는 것.

    PDF ── extract ──▶ 조각(역할 태깅 + 비식별) ── embeddings ──▶ store(색인 JSON)
                                                                      │
                              ④ a4_draft ◀── retriever(역할별 top-k) ─┘

## 경계선

색인에 들어가는 것은 문장의 골격이다. 사업장·소재지·성명이 든 1·2장은 아예 읽지 않고,
남은 본문에서도 날짜·시각·주소·인명 라벨은 ○ 로 지운다(``extract.redact``). 검색 결과는
"견본" 이라는 라벨과 경고문을 달고 프롬프트에 들어가며, ``draft/v3`` 의 작성 규칙이
내용 차용을 다시 한 번 막는다. **한 겹으로 막지 않는다 — 색인·검색·프롬프트 세 곳이다.**

## 쓰는 법

    from app.rag import retriever
    bundle = retriever.retrieve("바닥 타일 파손 모서리 돌출 …")
    bundle.text          # 프롬프트에 그대로 넣는 블록
    bundle.exemplar_ids  # 어떤 견본이 쓰였는지 (추적·응답에 남긴다)

색인 만들기: ``python scripts/build_rag_index.py``
"""

from __future__ import annotations

from app.rag.retriever import StyleBundle, retrieve, status

__all__ = ["StyleBundle", "retrieve", "status"]
