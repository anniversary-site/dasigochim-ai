# 로컬 개발 가이드

[← README 로 돌아가기](../README.md)

---

## 설치와 실행

```bash
python3.12 -m venv .venv
source .venv/bin/activate                 # Windows: .\.venv\Scripts\activate
pip install -r requirements-dev.txt       # 런타임 + pillow + pypdf
cp .env.example .env                      # GEMINI_API_KEY 채우기, 처음엔 BACKEND_MODE=stub 권장

uvicorn app.main:app --host 127.0.0.1 --port 8000
# 또는: python -m app.main   (HOST / PORT / LOG_LEVEL 환경변수를 따름)
```

> `.env.example` 의 `BACKEND_MODE` 는 `http` 로 적혀 있습니다. 백엔드 없이 혼자 돌려 보려면 `.env` 에서 `stub` 으로 바꿔 주세요 (코드 기본값은 `stub`).

- Swagger UI: <http://127.0.0.1:8000/docs>
- 헬스체크: `GET /health` — 모델, 백엔드 모드, 키 로드 여부, 프롬프트 소스, Langfuse·문체 RAG 상태를 한눈에 보여 줍니다.

기동 시 세 가지를 확인하고 로그를 남깁니다 (`app/main.py`).

| 확인 | 실패하면 |
|---|---|
| 활성 프롬프트 전부 로드 (`check_prompts`) | **기동 실패** — `defaults.yaml` 오타나 빠진 파일을 바로 드러냄 |
| 문체 색인 (`check_style_index`) | 경고만. ④ 는 문체 참조 없이 동작 |
| Langfuse 인증 (`check_langfuse_auth`) | 에러 로그만. 판독은 그대로 |

모든 응답에는 `X-Request-Id` 헤더가 붙습니다 (요청에 있으면 그 값을, 없으면 새로 만듭니다).

---

## 전체 플로우를 한 번에 — `scripts/e2e.py`

서버를 띄운 뒤 다른 터미널에서:

```bash
python scripts/e2e.py                          # 합성 샘플 사진 (scripts/sample.jpg)
python scripts/e2e.py --photo ./현장.jpg        # 실제 사진 (권장)
python scripts/e2e.py --photo https://.../signed.jpg --skip-regenerate
```

`①` → `②`×3 → `③` → `④`(신규) → `④`(재생성) → `⑤` 순으로 호출하고 전 단계 응답을 `scripts/out/<session_id>.json` 에 남깁니다.
기존 측정으로 1회 전체 약 20~25초가 걸렸습니다.

주요 옵션: `--base-url`(기본 `http://127.0.0.1:8000`), `--location`, `--building` · `--floor`(⑤ 반복 집계 대상), `--description`, `--question-count`, `--comment`(재생성 코멘트), `--out`.
기본 위치·건물(`김대건관 2층`)은 stub 픽스처 `cases.json` 과 맞춰 둔 값이라, ⑤ 의 반복 발생 집계가 실제로 잡힙니다.

합성 샘플 사진은 `python scripts/make_sample.py` 로 다시 만들 수 있습니다 (`pillow` 필요).

---

## `/docs` 에서 사진 올려 시험하기

`POST /v1/vision/analyze` 는 JSON 본문(`photo.url` / `photo.base64`)이라 Swagger UI 에 파일 선택 버튼이 뜨지 않습니다.
그래서 같은 로직의 **파일 업로드 판**을 따로 뒀습니다.

`POST /v1/vision/analyze-upload` → **Try it out** → `file` 의 **파일 선택** → Execute

- `multipart/form-data`, `image/*` 만 · 10MB 까지 (초과 413, 형식 불일치 415)
- `location_text` · `description` · `session_id` 는 폼 필드이고 비워도 기본값으로 돕니다
- `prompt_version` 을 채우면 그 버전으로만 1회 판독 — 프롬프트 A/B 를 재시작 없이 비교
- 운영 연동은 JSON 본문을 쓰는 `POST /v1/vision/analyze` 쪽입니다

---

## 테스트

```bash
python -m unittest discover -s tests
# pytest 가 설치되어 있다면: python -m pytest tests
```

`tests/test_draft_qa_reflection.py` 는 ④ 의 질의응답 반영 검증·보정 경로를 `unittest.mock` 으로 LLM 없이 검사합니다.
테스트 러너는 requirements 에 포함되어 있지 않습니다 (표준 라이브러리 `unittest` 기반).

---

## 스크립트 모음

| 스크립트 | 용도 | 문서 |
|---|---|---|
| `scripts/e2e.py` | end-to-end 러너 | 위 |
| `scripts/sync_prompts.py` | 프롬프트 YAML → Langfuse 업로드 | [langfuse.md](langfuse.md) |
| `scripts/build_rag_index.py` | 재해조사의견서 PDF → 문체 색인 | [style-rag.md](style-rag.md) |
| `scripts/make_sample.py` | 합성 샘플 사진 생성 | — |

스크립트는 Docker 이미지에 들어가지 않습니다 (`.dockerignore`).

---

## 디렉터리 구조

```
app/
  main.py              FastAPI 조립, 예외 → 502 매핑, 요청 로깅, 프롬프트·RAG 관리 API
  config.py            환경변수 로드 (Dify env.* 와 1:1)
  common.py            공유 입력 조각 (VisionResult, PhotoInput, qa_history 조립)
  cases.py             C-5 과거사례 검색 + 목록·집계 조립 (④⑤ 공용)
  llm/gemini.py        LLM 노드 1회 = generate_structured() 1회. 재시도·타임아웃 포함
  llm/tracing.py       Langfuse 추적. 키가 없으면 전부 no-op
  llm/masking.py       트레이스 개인정보 마스킹
  apps/a1..a5_*.py     API 1개 = 파일 1개 (노드 배선 · 요청/응답 모델 · 라우터)
  prompts/             프롬프트는 코드가 아니라 여기 있습니다
    __init__.py          로더 · 버전 결정 · 카탈로그
    langfuse_source.py   Langfuse 연동 (실패 시 파일로 폴백)
    defaults.yaml        노드별 활성 버전
    <노드>/vN.yaml       system · user · schema · temperature · changelog
  rag/                 ④ 문체 RAG
    extract.py           PDF → 조각 (역할 태깅 · 비식별)
    profile.py           코퍼스 문체 통계
    embeddings.py        Gemini 임베딩 + 해시 대체물
    store.py             색인 파일 읽기·쓰기·코사인 검색
    retriever.py         역할별 top-k → 프롬프트 블록 조립
    index/               빌드 산출물 (git 에 넣습니다)
  backends/
    base.py              C-1 / C-2 / C-5 계약
    stub_client.py       로컬 픽스처 (BACKEND_MODE=stub)
    http_client.py       실제 Spring 백엔드 (BACKEND_MODE=http)
    fixtures/            departments.json · cases.json · places.json
scripts/               e2e · sync_prompts · build_rag_index · make_sample
tests/                 unittest
docs/                  고용노동부 재해조사의견서 12건 (문체 참조 원본 PDF)
readme/                이 문서들
API_명세서.md          요청·응답 상세 명세
노드별설명.md          원본 Dify 앱 노드·프롬프트 (자동 생성 문서)
```
