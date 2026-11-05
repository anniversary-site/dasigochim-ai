# 설정 — 환경변수, 백엔드 모드, 내부 계약

모든 설정은 `app/config.py` 의 `Settings`(pydantic-settings) 한 곳에서 읽습니다.

- 저장소 루트의 `.env` 를 읽고 (UTF-8), 프로세스 환경변수가 있으면 그쪽이 우선합니다.
- 키 이름은 **대소문자를 가리지 않습니다** (`case_sensitive=False`). 모르는 키는 무시합니다 (`extra="ignore"`).
- 시작점은 `.env.example` 입니다. `cp .env.example .env` 후 값을 채우세요. `.env` 는 git 과 Docker 이미지 양쪽에서 제외됩니다.

[← README 로 돌아가기](../README.md)

> 이 문서에는 비밀값을 적지 않습니다. `GEMINI_API_KEY`, `AGENT_TOKEN`, `LANGFUSE_*_KEY` 는 각자 발급받은 값을 `.env` 나 배포 환경에만 넣어 주세요.

---

## 환경변수 전체 목록

### LLM (Gemini)

| 키 | 기본값 | 설명 |
|---|---|---|
| `GEMINI_API_KEY` | (빈 값) | **필수.** 비어 있으면 첫 LLM 호출에서 `LlmError` → 502. `GET /health` 의 `api_key_loaded` 로 확인 |
| `MODEL_NAME` | `gemini-3.1-flash-lite` | 모든 LLM 노드가 쓰는 모델 |
| `THINKING_LEVEL` | `low` | `low` / `high` / `off`. `off` 면 thinking 설정을 넣지 않음 (gemini-3 계열에서만 의미 있음) |
| `LLM_TIMEOUT_SECONDS` | `60.0` | LLM 호출 1회 타임아웃 |
| `LLM_MAX_RETRIES` | `2` | JSON 파손·빈 응답·429/5xx 에 재시도하는 횟수 (최대 시도 = 값 + 1) |

### 백엔드 연동

| 키 | 기본값 | 설명 |
|---|---|---|
| `BACKEND_MODE` | `stub` | `stub` = 로컬 픽스처, `http` = 실제 Spring 백엔드 ([아래](#backend_mode--stub-vs-http)) |
| `BACKEND_BASE_URL` | `http://localhost:8080` | `http` 모드에서 C-1/C-2/C-5 를 부를 백엔드 주소 |
| `AGENT_TOKEN` | `dev-agent-token` | 백엔드 내부 API 호출 시 `X-Agent-Token` 헤더 값. 운영에서는 반드시 바꿀 것 |
| `BACKEND_TIMEOUT_SECONDS` | `10.0` | 백엔드 호출 타임아웃 |

### stub 모드 부서 판정 정책

`http` 모드에서는 백엔드(C-2)가 결정하므로 쓰이지 않습니다.

| 키 | 기본값 | 설명 |
|---|---|---|
| `DEPARTMENT_CONFIDENCE_THRESHOLD` | `0.7` | 이 값 미만이면 fallback |
| `DEFAULT_DEPARTMENT_CODE` | `SAFETY_CENTER` | fallback 부서 코드. `app/backends/fixtures/departments.json` 에 있어야 함 |

### ② 객체 확인 질문 · ⑤ 반복 발생 판정

| 키 | 기본값 | 설명 |
|---|---|---|
| `OBJECT_CONFIRM_THRESHOLD` | `0.7` | ① 의 `object_confidence` 가 이 값 미만이면 ② 의 첫 질문을 "이 객체가 맞는지" 로 바꿈 |
| `CASE_WINDOW_DAYS` | `30` | ⑤ 반복 발생 집계 기간(일). C-5 요청의 `window_days` 로 실림 |
| `RECURRING_THRESHOLD` | `3` | 이번 건을 **포함한** 동일 건물 신고 횟수가 이 값 이상이면 `is_recurring = true` |

### ④ 문체 RAG

자세한 동작은 [style-rag.md](style-rag.md).

| 키 | 기본값 | 설명 |
|---|---|---|
| `RAG_ENABLED` | `true` | 끄면 `draft/v3` 가 문체 참조 없이 `[작성 규칙]` 만으로 씀 |
| `RAG_INDEX_PATH` | (빈 값) | 비우면 `app/rag/index/style_index.json` |
| `RAG_DOCS_DIR` | (빈 값) | 비우면 저장소의 `docs/` (색인 빌드 때만 씀) |
| `RAG_TOP_K_PER_ROLE` | `2` | 역할(관찰·위험·원인·조치)마다 붙일 견본 수 |
| `RAG_MAX_CHARS_PER_EXEMPLAR` | `260` | 견본 1개 최대 길이 |
| `EMBEDDING_MODEL` | `gemini-embedding-001` | 문체 견본 검색용 임베딩 모델 |
| `EMBEDDING_DIMENSIONS` | `768` | 임베딩 차원 (`output_dimensionality`) |

### 프롬프트 소스 · 버전

자세한 동작은 [pipeline.md](pipeline.md#프롬프트-버전-관리) / [langfuse.md](langfuse.md).

| 키 | 기본값 | 설명 |
|---|---|---|
| `PROMPT_SOURCE` | `file` | `file` = `app/prompts/*.yaml`, `langfuse` = Langfuse (실패 시 파일로 폴백) |
| `PROMPT_VERSIONS` | (빈 값) | 노드별 버전 강제. 예: `vision=v2,question=v1`. 형식이 틀리면 `PromptError` |
| `PROMPT_CACHE_TTL_SECONDS` | `60` | Langfuse 모드의 프롬프트 캐시 TTL |

### Langfuse

| 키 | 기본값 | 설명 |
|---|---|---|
| `LANGFUSE_PUBLIC_KEY` | (빈 값) | `pk-lf-…`. 두 키가 다 있어야 Langfuse 기능이 켜짐 (`langfuse_ready`) |
| `LANGFUSE_SECRET_KEY` | (빈 값) | `sk-lf-…` |
| `LANGFUSE_BASE_URL` | `https://cloud.langfuse.com` | Langfuse 호스트 |
| `LANGFUSE_PROMPT_PREFIX` | `dasigochim` | Langfuse 프롬프트 이름 접두사 (`dasigochim/vision`) |
| `LANGFUSE_TRACING` | `true` | `false` 면 추적 끔 (`PROMPT_SOURCE` 와 무관) |
| `LANGFUSE_TRACING_ENVIRONMENT` | `local` | 트레이스 환경 이름. `local` / `demo` / `prod` 로 갈라 UI 에서 거름 |
| `LANGFUSE_MASK_PII` | `true` | 트레이스로 나가는 값에서 이메일·전화번호 등 구조화된 개인정보를 `[제거됨]` 으로 지움 |

### 서버

| 키 | 기본값 | 설명 |
|---|---|---|
| `HOST` | `127.0.0.1` | `python -m app.main` 으로 띄울 때의 바인드 주소. 컨테이너에서는 `0.0.0.0` (Dockerfile·compose 가 덮어씀) |
| `PORT` | `8000` | 포트 |
| `LOG_LEVEL` | `info` | 로그 레벨 |

> `uvicorn app.main:app --host … --port …` 로 직접 띄우면 `HOST` / `PORT` 대신 uvicorn 인자가 쓰입니다.
> Docker 이미지의 `CMD` 도 uvicorn 을 직접 부르며 `0.0.0.0:8000` 을 명시합니다.

### `compose.yaml` 전용 오버라이드

로컬 `docker compose` 에서는 `.env` 를 `env_file` 로 읽은 뒤, 아래 변수로 몇 가지를 덮어씁니다.

| 변수 | 기본값 | 덮어쓰는 대상 |
|---|---|---|
| `AI_BACKEND_MODE` | `stub` | 컨테이너의 `BACKEND_MODE` |
| `AI_BACKEND_BASE_URL` | `http://host.docker.internal:8080` | 컨테이너의 `BACKEND_BASE_URL` |
| `AI_PORT` | `8000` | 호스트에 여는 포트 |

> **왜 `AI_` 접두사인가요?** Compose 는 같은 `.env` 를 `${...}` 보간에도 씁니다. `${BACKEND_BASE_URL:-...}` 라고 적으면
> `.env` 의 `localhost:8080` 이 먼저 잡혀 **컨테이너가 자기 자신을 가리키게** 됩니다. 그래서 이름을 갈랐습니다.

---

## `BACKEND_MODE` — stub vs http

③ ④ ⑤ 는 백엔드 데이터(부서 업무범위, 과거 신고)가 필요합니다. 어디서 가져올지를 이 값이 정합니다 (`app/backends/__init__.py` 의 `get_backend()`).

| | `stub` (기본) | `http` |
|---|---|---|
| 구현 | `app/backends/stub_client.py` | `app/backends/http_client.py` |
| 데이터 | `app/backends/fixtures/departments.json` · `cases.json` | Spring 백엔드 `/internal/v1/*` |
| C-1 업무범위 검색 | 문자 bigram 자카드 유사도 (결정론적, 재현 가능) | 백엔드 응답 그대로 |
| C-2 부서 확정 | `DEPARTMENT_CONFIDENCE_THRESHOLD` / `DEFAULT_DEPARTMENT_CODE` 로 흉내 | 백엔드가 결정 |
| C-5 과거사례 | **백엔드 SQL 의 참조 구현** — 동일 건물 우선 → 최근순, `score` 는 null. `days_ago` 를 오늘 기준 날짜로 펼침 | 백엔드 응답 그대로 |
| 쓰는 때 | 백엔드 없이 AI 서버만 개발·시험 | 통합 실행, 운영 |

- 그 외 값을 넣으면 `BackendError("알 수 없는 BACKEND_MODE")` 가 납니다.
- 부서 픽스처 코드(`FACILITY` / `SAFETY_CENTER` / `GENERAL_AFFAIRS`)는 백엔드 시드와 맞춰 두어, `http` 로 넘어가도 코드가 어긋나지 않게 했습니다.
- 운영 배포(dasigochim-deployment 의 `compose.prod.yaml`)는 `BACKEND_MODE=http`, `BACKEND_BASE_URL=http://backend:8080` 으로 띄웁니다.
- `app/backends/fixtures/places.json` 은 위치 정규화용 장소 레지스트리 픽스처인데, 현재 `app/` 코드에서 읽는 곳은 없습니다.

---

## 백엔드 내부 계약 (C-1 / C-2 / C-5)

`http` 모드에서 AI 서버가 Spring 백엔드에 거는 호출입니다. 계약은 `app/backends/base.py` 의 `BackendClient` 프로토콜에 정의되어 있습니다.

- 모두 `POST`, 본문 JSON (`json.dumps(ensure_ascii=False)` 로 조립 — 제보자 발화에 따옴표가 섞여도 안전)
- 헤더 `X-Agent-Token: <AGENT_TOKEN>`
- HTTP 200 이 아니거나 JSON 파싱에 실패하면 `BackendError`

백엔드 쪽은 dasigochim-backend 의 `InternalAiController`(`/internal/v1`)가 받고, `AgentTokenFilter` 가 `X-Agent-Token` 을 검증합니다.

### C-1 `POST /internal/v1/rag/duty-search` — 부서 업무범위 검색 (③)

```jsonc
// 요청
{"session_id": "...", "query_text": "위험요소 사진분석 위험성 노출대상 위치", "top_k": 8}
// 응답
{"candidates": [{"code": "FACILITY", "name": "시설팀", "duty_text": "...", "score": 0.82}],
 "corpus_version": 1}
```

**실패하면 ③ 전체가 실패합니다** (부서 판정의 필수 입력 — 의도된 동작).

### C-2 `POST /internal/v1/departments/resolve` — 부서 확정 / fallback (③)

```jsonc
// 요청
{"session_id": "...", "department_code": "FACILITY", "confidence": 0.91, "rationale": "..."}
// 응답
{"department": {"code": "FACILITY", "name": "시설팀", "contact": "..."},
 "assigned_by": "agent",      // 임계값 미달·무효 코드면 "fallback"
 "confidence": 0.91,          // fallback 이면 null
 "rationale": "..."}
```

코드 유효성 검증과 임계값 fallback 은 결정론이라 백엔드 몫입니다. 응답에 `department.code` 가 없으면 AI 서버가 `BackendError` 를 냅니다.

### C-5 `POST /internal/v1/cases/search` — 과거사례 + 반복 집계 (④⑤)

```jsonc
// 요청 — 값이 없는 위치 필드(report_id, building_id, building_name, floor)는 빼고 보냅니다
{"session_id": "...", "query_text": "...", "top_k": 4, "window_days": 30,
 "report_id": "...", "building_id": 1, "building_name": "...", "floor": "2"}
// 응답
{"cases": [{"report_id": "R-2025-0412", "occurred_at": "2025-04-12", "score": null,
            "status": "RECEIVED", "building_name": "...", "floor": "2",
            "hazard": "...", "action_fundamental": "..."}],
 "stats": {"window_days": 30, "building_name": "...", "floor": "2",
           "total": 2, "unresolved": 1, "same_floor": 1,
           "risk_distribution": {"medium": 2}, "first_occurred_at": "2025-04-01"}}  // 또는 null
```

- `cases` — 관련 과거 신고 목록. 1단계는 "동일 건물 우선 → 최근순" 이고 `score` 는 null 입니다. 2단계에서 `query_embedding` 유사도 순으로 바뀌어도 응답 계약은 그대로입니다.
- `stats` — 동일 건물·기간 집계. 순수 SQL 이라 LLM 도 임베딩도 끼지 않는 확정 사실이며, ⑤ 반복 발생 판정의 근거입니다. `total` 은 **이번 신고를 제외한** 건수입니다. 건물 정보가 없으면 `null`.
- `status` 값은 `RECEIVED`(접수) / `REVIEWING`(처리중) / `RESOLVED`(처리완료) 로 해석합니다 (`app/cases.py`).
- **실패해도 ④⑤ 는 계속 진행합니다.** 과거사례는 부가 정보라 `(과거사례 조회 실패 — 참고 없이 작성)` 으로 우회합니다.
