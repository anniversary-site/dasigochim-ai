# ④ 문체 RAG — 재해조사의견서에서 '쓰는 법' 만 가져오기

[← README 로 돌아가기](../README.md) · 관련: [pipeline.md ④](pipeline.md#-신고서-초안-생성--a4_draft) · [configuration.md](configuration.md#-문체-rag)

`docs/*.pdf` 는 고용노동부 **재해조사의견서 12건**입니다 ([`../docs/`](../docs/)).
사고 내용은 산업재해(톤백·지게차·축사 지붕 등)라 다시고침이 다루는 주거 시설 민원과 겹치지 않습니다.
그런데도 이 문서를 두는 이유는 딱 하나 — **감독관이 사실을 적는 방식**을 ④ 신고서 초안이 따라 하게 하려는 것입니다.

```
docs/*.pdf ─ extract ─▶ 조각(역할 태깅·비식별) ─ embeddings ─▶ app/rag/index/style_index.json
                                                                        │
                                    ④ a4_draft ◀── retriever(역할별 top-k) ┘
```

| 모듈 | 하는 일 |
|---|---|
| `app/rag/extract.py` | PDF → 조각. 역할 태깅(관찰·위험·원인·조치·경위)과 비식별 |
| `app/rag/profile.py` | 코퍼스 문체 통계 (종결어미 분포 등) → `[문체 규격]` |
| `app/rag/embeddings.py` | Gemini 임베딩 + 해시 대체물 |
| `app/rag/store.py` | 색인 파일 읽기·쓰기·코사인 검색 |
| `app/rag/retriever.py` | 역할별 top-k 검색 → 프롬프트 블록 조립 |
| `app/rag/index/` | 빌드 산출물 (git 에 넣습니다) |

현재 커밋된 색인: 문서 12건 · 조각 186개 · 임베더 `gemini:gemini-embedding-001:768` · 생성 2026-09-01.

---

## 무엇을 가져오고 무엇을 안 가져오나

**가져오는 것은 문장의 골격입니다.** 개조식 종결, **확인과 추정을 어미로 가르는 습관**
(`~임` / `~것으로 추정됨`), `[상태]→[사고 형태]→[피해]` 로 이어지는 위험 서술,
`[대상]+[방법]+[기준]` 으로 닫는 조치 문장.

**내용은 가져오지 않습니다.** 한 겹이 아니라 세 곳에서 막습니다.

| 어디서 | 무엇을 |
|---|---|
| 색인 (`extract.py`) | 1·2장(사업장·인적사항)은 **아예 읽지 않습니다** (3·4·5장만 색인). 남은 본문의 날짜·시각·주소·인명은 `○` 로 가립니다 |
| 검색 (`retriever.py`) | 견본을 짧게 자르고(기본 260자), 같은 사건에서 몰아 뽑지 않습니다. 블록 머리에 "문장 구조·어미·서술 순서만 본뜰 것" 경고문을 붙입니다 |
| 프롬프트 (`draft/v3`) | 참조의 사물·수치·조치·법령·호칭을 옮기는 것은 **오답**이라고 명시합니다 |

`[유사 과거사례]`(같은 주택의 실제 민원 기록, 내용 참고 가능)와 `[문체 참조]`(남의 문서, 문체만)를
프롬프트에서 라벨로 갈라 놓은 것이 draft v3 의 핵심입니다.

견본은 신고서 항목별로 붙습니다 (`RETRIEVAL_PLAN`).

| 역할 | 붙는 항목 |
|---|---|
| observation | `photo_analysis` — 관찰된 상태를 적는 문체 |
| hazard | `hazard` · `risk` — 위험 요소를 짚고 위험성으로 잇는 문체 |
| cause | `risk` — 원인을 인과로 풀어 쓰는 문체 |
| action | `action_urgent` · `action_fundamental` — 조치를 제안하는 문체 |

---

## 색인 만들기

```bash
python scripts/build_rag_index.py                    # Gemini 임베딩 (GEMINI_API_KEY 필요)
python scripts/build_rag_index.py --embedder hash    # 키 없이 (CI·오프라인)
python scripts/build_rag_index.py --dry-run          # 무엇이 색인될지만 출력
python scripts/build_rag_index.py --dry-run --role action   # 특정 역할만 보기
```

PDF 파싱에는 `pypdf` 가 필요해 `requirements-dev.txt` 로 설치합니다 (런타임은 색인 JSON 만 읽습니다).

**색인은 빌드 산출물이지만 git 에 넣습니다.** 받아 온 사람도, CI 도, 컨테이너도 PDF 를 다시 파싱하지 않고 같은 견본을 보게 하려는 것입니다 —
프롬프트가 환경마다 달라지는 쪽이 더 나쁩니다. Docker 이미지에는 `docs/` 가 빠지고 `app/rag/index/` 만 들어갑니다.

**PDF 를 갈아 끼웠으면 이 스크립트를 다시 돌려 주세요.** 색인은 저절로 갱신되지 않습니다.
색인 빌드는 쿼터에 걸리면 기다렸다가 다시 부릅니다 (최대 5회) — 절반만 만들어진 색인이 더 나쁘기 때문입니다.

---

## 실패해도 초안은 나옵니다

과거사례(`app/cases.py`)와 같은 태도입니다. retriever 는 예외를 밖으로 내보내지 않고, 검색 순위가 단계적으로 내려갑니다.
결과는 ④ 응답의 `style_reference_source` 에 남습니다.

| `style_reference_source` | 언제 |
|---|---|
| `embedding` | 정상. 색인과 같은 방식으로 질의를 임베딩했습니다 |
| `hash` | 임베딩 API 가 막혔습니다(쿼터·네트워크). 색인에 함께 구워 둔 해시 벡터로 갈아탑니다 |
| `role-order` | 쓸 벡터가 없습니다. 순위를 포기하고 역할별 원문 순서로 뽑습니다 |
| `none` | 색인이 없거나 `RAG_ENABLED=false`, 또는 draft 프롬프트에 `{style_reference}` 가 없음(v1·v2). `[작성 규칙]` 만으로 씁니다 |

임베딩이 한 번 막히면 **60초간** 시도를 쉽니다 — 요청마다 왕복 약 350ms 를 버리지 않으려는 것입니다.
조회 쪽은 재시도하지 않습니다 (④ 는 기다리면 안 되므로).

기동 시 `app/main.py` 의 `check_style_index` 가 색인 상태를 로그 한 줄로 남깁니다. 색인이 없으면 경고가 찍힙니다.

---

## 들여다보기

| 엔드포인트 | 용도 |
|---|---|
| `GET /v1/rag/status` | 색인 상태 · 역할별 조각 수 · 코퍼스에서 집계한 문체 규격 |
| `GET /v1/rag/style?query=…&top_k=…` | ④ 프롬프트에 **실제로 들어갈** 견본 블록을 그대로 |
| `POST /v1/rag/reload` | 색인을 다시 만든 뒤 캐시만 비움 |

`GET /health` 의 `style_rag` 로 견본을 붙일 수 있는 상태인지 빠르게 볼 수 있습니다.
④ 응답의 `style_reference_ids` 로 어떤 견본이 그 초안에 붙었는지 되짚을 수 있습니다 (`similar_case_ids` 를 남기는 이유와 같습니다).
