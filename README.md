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
  main.py            FastAPI 조립, 예외 → 502 매핑, 요청 로깅, 프롬프트 관리 API
  config.py          .env 로드 (Dify env.* 와 1:1)
  common.py          공유 입력 조각 (VisionResult, PhotoInput, qa_history 조립)
  cases.py           C-5 과거사례 검색 + 목록 조립 (④⑤ 공용)
  llm/gemini.py      LLM 노드 1회 = generate_structured() 1회. 재시도·타임아웃 포함
  llm/tracing.py     Langfuse 추적. 키가 없으면 전부 no-op (판독을 막지 않는다)
  apps/a1..a5.py     API 1개 = 파일 1개 (노드 배선 · 요청/응답 모델 · 라우터)
  prompts/           **프롬프트는 코드가 아니라 여기 있다** (아래 절 참고)
    __init__.py      로더 · 버전 결정 · 카탈로그
    langfuse_source.py  Langfuse 연동 (실패 시 파일로 폴백)
    defaults.yaml    노드별 활성 버전
    <노드>/vN.yaml   system · user · schema · temperature · changelog
  backends/
    base.py          C-1 / C-2 / C-5 계약
    stub_client.py   로컬 픽스처 (BACKEND_MODE=stub)
    http_client.py   실제 Spring 백엔드 (BACKEND_MODE=http)
    fixtures/        departments.json · cases.json · places.json
scripts/
  e2e.py             end-to-end 러너
  sync_prompts.py    프롬프트 YAML → Langfuse 업로드
  make_sample.py     합성 샘플 사진 생성기
```

## 프롬프트 관리

프롬프트를 파이썬 상수에서 떼어내 **LLM 노드 1개 = 디렉토리 1개 = 버전 파일 N개**로 옮겼다.
`app/apps/*.py` 에는 이제 배선과 요청/응답 모델만 있다.

| 노드 | 쓰는 곳 | 버전 |
|---|---|---|
| `vision` | ① 사진 판독 | v1 Dify 원문 · v2 제보자 설명 우선 · **v3 객체 식별 허들** |
| `question` | ② 확인 질문 | v1 Dify 원문 · **v2 질문 축 로테이션** |
| `object_check` | ② 객체 확인 질문 | **v1 (신규 노드)** |
| `department` · `risk` · `draft` · `summary` · `insight` | ③④⑤ | v1 |

### 버전 고르기 — 좁은 것이 이긴다

1. **요청 본문** `prompt_versions` — 그 호출 1회에만 적용. 재시작 불필요
   ```jsonc
   {"session_id": "...", "prompt_versions": {"vision": "v2"}}
   ```
2. **`.env`** `PROMPT_VERSIONS="vision=v2,question=v1"` — 이 서버 전체
3. **`app/prompts/defaults.yaml`** 의 `active` — 기본값

`POST /v1/vision/analyze-upload` 에는 `prompt_version` 폼 필드가 있어 `/docs` 에서
사진을 올리며 버전만 바꿔 바로 비교할 수 있다.

| 엔드포인트 | 용도 |
|---|---|
| `GET /v1/prompts` | 노드별 활성 버전 · 고를 수 있는 버전 · changelog |
| `GET /v1/prompts/{노드}/{버전}` | 그 버전의 system · user · schema 전문 |
| `POST /v1/prompts/reload` | YAML 을 고친 뒤 캐시만 비운다 (재시작 없이 반영) |

활성 버전이 하나라도 안 읽히면 **기동 시점에 터진다.** 첫 호출 때 502 로 나타나면
원인을 찾기 어렵기 때문이다.

### Langfuse (`PROMPT_SOURCE=langfuse`)

**git 의 YAML 이 진실이고 Langfuse 는 그 사본이다.** 동기화는 한 방향뿐이다.

```powershell
.\.venv\Scripts\python.exe scripts\sync_prompts.py --dry-run   # 무엇이 올라갈지
.\.venv\Scripts\python.exe scripts\sync_prompts.py             # 올리기
```

파일 버전 `vN` 이 Langfuse **label** 로 올라가므로, 위 세 경로가 파일 모드와
Langfuse 모드에서 **같은 식별자**를 쓴다. `defaults.yaml` 의 활성 버전에는
`production` 라벨도 함께 붙는다. `temperature` 와 JSON 스키마는 `config` 에 실어
버전과 함께 고정한다.

Langfuse 가 죽거나 키가 비면 **파일 소스로 자동 폴백한다** (경고 로그 1줄).
시연 중 외부 서비스 때문에 판독이 멈추지 않게 하려는 것이다.

변수 치환은 Langfuse 의 `compile()`(`{{var}}`) 이 아니라 파일 모드와 같은
`str.format()`(`{var}`) 을 쓴다. 두 소스가 같은 렌더링을 타야 "Langfuse 에서만
다르게 나온다" 는 버그가 안 생긴다. 대신 Langfuse UI 의 변수 자동 인식은 못 쓴다 —
의도한 맞바꿈이다.

### 추적 (Langfuse tracing)

`LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` 만 채우면 켜진다.
**`PROMPT_SOURCE` 와는 무관하다** — 프롬프트를 파일에서 읽어도 추적은 돈다.

```
API 호출 1회 = 트레이스 1개 (trace_name = "⑤ 요약·인사이트")
  └─ generation "1805000005 제목·요약 [summary/v1]"
  └─ generation "1805000006 인사이트 [insight/v1]"
```

generation 에 프롬프트 이름·버전·소스, 토큰 사용량, temperature 가 붙는다.
`session_id` 는 트레이스 속성으로 올라가 Langfuse **Sessions** 에서 신고 한 건이
①→②×3→③→④→⑤ 로 묶여 보인다. 어느 프롬프트 버전이 어떤 출력을 냈는지가
자동으로 쌓이므로 `prompt_versions` A/B 의 근거가 된다.

**추적 실패는 판독을 막지 않는다.** 키가 없거나 Langfuse 가 죽으면 전부 no-op 으로
떨어지고 경고는 한 번만 찍는다. 사진 바이트는 싣지 않는다 (`has_image` 플래그만).
`LANGFUSE_TRACING=false` 로 끌 수 있다.

`GET /health` 의 `langfuse_tracing` 으로 켜졌는지 확인한다.

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
- **① 프롬프트는 문서와 의도적으로 다르다.** `노드별설명.md` 의 원문은 "사진에서 확인되는",
  "사진에서 실제로 보이는 상태만" 이라고 못 박아 모델이 제보자 부연 설명을 통째로 버렸다.
  현장을 본 사람은 제보자뿐이고, 사진에 안 담기는 정보(밟으면 덜컹거림, 냄새, 언제부터인지)가
  판독의 핵심인 경우가 많다. 그래서 **제보자 설명을 판독의 기준으로 올리고**, 사진은 그 진술을
  확인·구체화하는 증거로 재정의했다. 환각 방지는 "출처를 밝혀 적는다"로 대체했다 —
  `photo_analysis` 는 사진으로 확인한 것과 제보자에게 들은 것을 문장 안에서 구분해 쓴다.
  ④⑤ 가 `photo_analysis` 를 그대로 인용하므로 이 구분이 유지되어야 한다.

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
