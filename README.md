# 안전고래 AI 서버 (프로토타입)

`노드별설명.md` 의 Dify 5개 앱을 **Dify 없이** FastAPI + Gemini 로 옮긴 것이다.
Dify 앱 1개 = REST 엔드포인트 1개 = 파이썬 모듈 1개로 대응한다.

| 엔드포인트 | 원본 Dify 앱 | 모듈 |
|---|---|---|
| `POST /v1/vision/analyze` | ① 사진 판독 | `app/apps/a1_vision.py` |
| `POST /v1/questions/next` | ② 확인 질문 생성 | `app/apps/a2_question.py` |
| `POST /v1/departments/classify` | ③ 담당 부서 판정 | `app/apps/a3_department.py` |
| `POST /v1/drafts/generate` | ④ 신고서 초안 생성 | `app/apps/a4_draft.py` |
| `POST /v1/insights/generate` | ⑤ 요약·인사이트 | `app/apps/a5_insight.py` |

시스템/유저 프롬프트와 구조화 출력 스키마는 문서의 값을 **문자 그대로** 옮겼다.
Dify 의 code 노드(선택지 3개 보정, 후보 목록 조립, 과거사례 조립)도 로직 그대로 포팅했다.

## 빠른 실행

```powershell
cd D:\DEV\ai
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

다른 터미널에서 전체 플로우를 한 번에:

```powershell
cd D:\DEV\ai
.\.venv\Scripts\python.exe scripts\e2e.py                      # 합성 샘플 사진
.\.venv\Scripts\python.exe scripts\e2e.py --photo C:\현장.jpg  # 실제 사진 (권장)
```

`①` → `②`×3 → `③` → `④`(신규) → `④`(재생성) → `⑤` 순으로 호출하고
전 단계 응답을 `scripts/out/<session_id>.json` 에 남긴다. 1회 전체 약 20~25초.

Swagger UI: <http://127.0.0.1:8000/docs> · 헬스체크: `GET /health`

### /docs 에서 사진 올려 시험하기

`POST /v1/vision/analyze` 는 JSON 본문(`photo.url` / `photo.base64`)이라 Swagger UI 에
파일 선택 버튼이 뜨지 않는다. 그래서 같은 로직의 **파일 업로드 판**을 따로 뒀다.

`POST /v1/vision/analyze-upload` → **Try it out** → `file` 의 **파일 선택** → Execute

`multipart/form-data` 로 받고, `image/*` 만 · 10MB 까지 허용한다(초과 시 413, 형식 불일치 415).
`location_text` · `description` · `session_id` 는 폼 필드이고 비워도 기본값으로 돈다.
운영 연동은 signed URL 을 쓰는 `POST /v1/vision/analyze` 쪽이다.

## 구조

```
app/
  main.py            FastAPI 조립, 예외 → 502 매핑, 요청 로깅
  config.py          .env 로드 (Dify env.* 와 1:1)
  common.py          공유 입력 조각 (VisionResult, PhotoInput, qa_history 조립)
  cases.py           C-5 과거사례 검색 + 목록 조립 (④⑤ 공용)
  llm/gemini.py      LLM 노드 1회 = generate_structured() 1회. 재시도·타임아웃 포함
  apps/a1..a5.py     API 1개 = 파일 1개 (프롬프트 · 스키마 · 노드 배선 · 라우터)
  backends/
    base.py          C-1 / C-2 / C-5 계약
    stub_client.py   로컬 픽스처 (BACKEND_MODE=stub)
    http_client.py   실제 Spring 백엔드 (BACKEND_MODE=http)
    fixtures/        departments.json · cases.json
scripts/
  e2e.py             end-to-end 러너
  make_sample.py     합성 샘플 사진 생성기
```

## Dify 와 달라진 점

- **워크플로 엔진 없음.** 노드 배선은 각 모듈의 `run()` 안 파이썬 흐름이다.
  ④ 의 `mode 분기`(IF/ELSE 노드)는 `if req.mode == "regenerate"` 한 줄이 됐다.
- **사진 입력이 넓어졌다.** `photo.url`(signed URL / 로컬 경로 / `data:` URI) 또는
  `photo.base64` 중 하나. 백엔드 연동 시에는 문서대로 signed URL 만 쓰면 된다.
- **`qa_history` 를 구조체로도 받는다.** `qa_history`(문자열, 백엔드 방식) 또는
  `qa_pairs`(`[{question, answer}]`, 실험 편의용). 문자열이 있으면 그쪽이 우선.
- **`confidence: -1`** 규칙은 유지했다. Dify number 출력이 null 을 못 담아서 생긴
  규칙이지만, 백엔드가 이미 이 값을 기대할 수 있어 그대로 뒀다. fallback = `-1`.
- **thinking_level=low.** gemini-3 계열 기본 추론을 낮춰 지연을 줄였다.
  판독 품질이 아쉬우면 `.env` 의 `THINKING_LEVEL=high` 로 올린다.

## 백엔드 연동 (`BACKEND_MODE`)

`stub`(기본)은 `app/backends/fixtures/` 를 문자 bigram 자카드 유사도로 검색한다.
Spring 쪽 엔드포인트 없이도 오늘 e2e 를 돌리기 위한 대역이다.

`http` 로 바꾸면 아래 3개를 실제로 호출한다. 모두 `X-Agent-Token` 헤더를 붙인다.
**Spring 백엔드에 아직 없는 엔드포인트이므로 구현이 필요하다.**

### C-1 `POST /internal/v1/rag/duty-search`
```jsonc
// 요청
{"session_id": "...", "query_text": "위험요소 사진분석 위험성 노출대상 위치", "top_k": 8}
// 응답
{"candidates": [{"code": "FACILITY", "name": "시설팀", "duty_text": "...", "score": 0.82}],
 "corpus_version": 1}
```
실패하면 ③ 전체가 실패한다 (부서 판정의 필수 입력 — 의도된 동작).

### C-2 `POST /internal/v1/departments/resolve`
```jsonc
// 요청
{"session_id": "...", "department_code": "FACILITY", "confidence": 0.91, "rationale": "..."}
// 응답
{"department": {"code": "FACILITY", "name": "시설팀", "contact": "02-705-8114"},
 "assigned_by": "agent",      // 임계값 미달·무효 코드면 "fallback"
 "confidence": 0.91,          // fallback 이면 null
 "rationale": "..."}
```
코드 유효성 검증과 임계값 fallback은 결정론이라 백엔드 몫이다.
stub 은 `DEPARTMENT_CONFIDENCE_THRESHOLD`(0.7) / `DEFAULT_DEPARTMENT_CODE` 로 흉내낸다.

### C-5 `POST /internal/v1/cases/search`
```jsonc
// 요청
{"session_id": "...", "query_text": "...", "top_k": 4}
// 응답
{"cases": [{"report_id": "R-2025-0412", "occurred_at": "2025-04-12", "score": 0.77,
            "hazard": "...", "action_fundamental": "..."}]}
```
**실패해도 ④⑤ 는 계속 진행한다** — 과거사례는 부가 정보라
`(과거사례 조회 실패 — 참고 없이 작성)` 으로 우회한다.

부서 픽스처의 코드는 백엔드 시드
(`V2__seed_departments_buildings.sql`: `FACILITY` / `SAFETY_CENTER` / `GENERAL_AFFAIRS`)와
맞춰 뒀다. `http` 모드로 넘어갈 때 코드가 어긋나지 않는다.

## 설정 (`.env`)

`.env.example` 참고. 주요 값:

| 키 | 기본값 | 설명 |
|---|---|---|
| `GEMINI_API_KEY` | — | 필수 |
| `MODEL_NAME` | `gemini-3.1-flash-lite` | 문서 지정 모델 |
| `THINKING_LEVEL` | `low` | `low` / `high` / `off` |
| `BACKEND_MODE` | `stub` | `stub` / `http` |
| `BACKEND_BASE_URL` | `http://localhost:8080` | Dify `env.BACKEND_BASE_URL` |
| `AGENT_TOKEN` | `dev-agent-token` | Dify `env.AGENT_TOKEN` |
| `LLM_MAX_RETRIES` | `2` | JSON 파손·5xx·429 에 재시도 |

## 컨테이너

`2026-08-30_인프라_시연배포_최종결정.md` 의 배포 기준을 따른다 —
AI 저장소는 **`Dockerfile` 을 관리**하고, `compose.yaml` 은 개발 편의용이다.
실배포는 `deployment/compose.prod.yaml` 이 GHCR 이미지를 내려받아 띄운다.

```powershell
cd D:\DEV\ai
docker compose up --build          # stub 모드, http://127.0.0.1:8000/docs
```

호스트에서 도는 Spring 과 붙여 시험하려면:

```powershell
$env:AI_BACKEND_MODE = "http"; docker compose up --build
```

> `AI_` 접두사를 쓴 이유가 있다. Compose 는 같은 `.env` 를 `${...}` 보간에도 쓰기 때문에
> `${BACKEND_BASE_URL:-...}` 라고 적으면 `.env` 의 `localhost:8080` 이 먼저 잡혀
> **컨테이너가 자기 자신을 가리킨다.** `AI_BACKEND_MODE` / `AI_BACKEND_BASE_URL` 로 오버라이드한다.

### 이미지 구성

| 항목 | 값 | 근거 |
|---|---|---|
| 베이스 | `python:3.12-slim` (2단계 빌드) | 빌드 도구·pip 캐시를 최종 이미지에서 뺀다 |
| 들어가는 것 | `app/` 과 런타임 의존성뿐 | `scripts/` · 문서 · `test_img.jpg` 는 `.dockerignore` 로 제외 |
| worker | **1개 고정** | `mem_limit: 160m` (t3.micro 1GiB 예산) |
| 포트 | `EXPOSE 8000`, 실배포는 `ports` 없이 `expose` 만 | 외부 비공개. `backend` 만 `http://ai:8000` 으로 호출 |
| 실행 사용자 | `appuser` (uid 10001) | root 로 돌리지 않는다 |
| 헬스체크 | `python -c urllib` 로 `/health`, 30s 간격 | slim 에 curl/wget 이 없다. 간격을 늘려 메모리 스파이크를 줄였다 |
| `MALLOC_ARENA_MAX=2` | glibc arena 축소 | 160m 예산에서 RSS 가 부는 것을 막는다 |

`pillow` 는 `scripts/make_sample.py` 전용이라 **런타임 의존성에서 뺐다.**
로컬 개발은 `requirements-dev.txt` 를 쓴다.

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

### 실배포 Compose 에 넣을 조각

`deployment/compose.prod.yaml` 담당자에게 전달할 부분이다.

```yaml
  ai:
    image: ghcr.io/safewhaleartechsogang/ai-server:${AI_IMAGE_TAG}
    restart: unless-stopped
    expose:
      - "8000"                      # ports 로 열지 않는다
    environment:
      HOST: 0.0.0.0
      GEMINI_API_KEY: ${GEMINI_API_KEY:?required}
      AGENT_TOKEN: ${AGENT_TOKEN:?required}
      BACKEND_MODE: ${AI_BACKEND_MODE:-stub}
      BACKEND_BASE_URL: http://backend:8080
    mem_limit: 160m
    depends_on:
      backend:
        condition: service_healthy
    # 볼륨 없음 — AI 서버는 무상태다
```

백엔드 쪽에는 `AI_BASE_URL: http://ai:8000` 을 넣는다.

> **EC2 에서 빌드하지 않는다.** CI 가 GHCR 에 이미지를 올리고 EC2 는 태그로 내려받는다.
> `ai/` 를 별도 저장소로 분리하고 GitHub Actions 워크플로를 붙이는 작업이 남아 있다.

## 남은 것

- **인증 없음.** AI 서버가 받는 쪽 `X-Agent-Token` 검증이 아직 없다.
  외부에 열기 전에 미들웨어를 붙여야 한다.
- **멱등성 없음.** 같은 `session_id` 로 두 번 부르면 두 번 호출된다.
- **`session_id` 는 로깅용.** 무상태 설계라 서버가 세션을 들고 있지 않다.
- ⑤ 는 백엔드가 초안 확정 후 **비동기로** 부르는 것을 전제한다 (여기서는 동기 HTTP).
- 부서 판정 임계값(0.7)과 top_k(8 / 4)는 문서의 기본값을 그대로 썼다. 실측 후 조정 필요.
