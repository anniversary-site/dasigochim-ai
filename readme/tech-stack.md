# 기술 스택

[← README 로 돌아가기](../README.md)

이 저장소에는 `pyproject.toml` 이 없습니다. 버전 정보의 출처는 `requirements.txt`(런타임), `requirements-dev.txt`(개발), `Dockerfile`, `app/config.py`, `.github/workflows/publish-image.yml` 입니다.
requirements 는 **하한(`>=`)만 고정**하고 lock 파일은 없으므로, 아래 버전은 "최소 요구 버전" 으로 읽어 주세요.

---

## 한눈에 보기

| 분류 | 기술 | 버전 / 값 | 출처 |
|---|---|---|---|
| 언어 | Python | **3.12** (`python:3.12-slim`) | `Dockerfile` |
| 웹 프레임워크 | FastAPI | `>=0.115` | `requirements.txt` |
| ASGI 서버 | Uvicorn (`[standard]`) | `>=0.30` | `requirements.txt` |
| 데이터 검증 | Pydantic | `>=2.7` | `requirements.txt` |
| 설정 | pydantic-settings | `>=2.3` | `requirements.txt` |
| LLM SDK | google-genai | `>=1.20` | `requirements.txt` |
| LLM 모델 | Gemini | `gemini-3.1-flash-lite` (기본) | `app/config.py` `MODEL_NAME` |
| 임베딩 모델 | Gemini Embedding | `gemini-embedding-001`, 768차원 | `app/config.py` |
| LLM 관측·프롬프트 관리 | Langfuse | `>=3.0` (코드는 SDK 4.15.1 기준으로 작성) | `requirements.txt`, `app/llm/tracing.py` |
| HTTP 클라이언트 | httpx | `>=0.27` | `requirements.txt` |
| 업로드 파싱 | python-multipart | `>=0.0.9` | `requirements.txt` |
| YAML | PyYAML | `>=6.0` | `requirements.txt` |
| .env | python-dotenv | `>=1.0` | `requirements.txt` |
| PDF 파싱 (개발) | pypdf | `>=5.0` | `requirements-dev.txt` |
| 이미지 생성 (개발) | Pillow | `>=10.0` | `requirements-dev.txt` |
| 테스트 | unittest (표준 라이브러리) | — | `tests/` |
| 컨테이너 | Docker (2단계 빌드), Docker Compose | — | `Dockerfile`, `compose.yaml` |
| CI/CD | GitHub Actions → GHCR | `ghcr.io/anniversary-site/ai-server` | `.github/workflows/publish-image.yml` |

---

## 무엇을 어디에 쓰나

### FastAPI · Uvicorn · Pydantic

- **FastAPI** — `app/main.py` 가 앱을 조립하고 `app/apps/a1..a5_*.py` 의 `APIRouter` 5개를 붙입니다. 요청 로깅 미들웨어(`X-Request-Id`), `LlmError` / `BackendError` → 502 예외 핸들러, startup·shutdown 훅, 프롬프트·RAG 관리 API 도 여기 있습니다. Swagger UI(`/docs`)는 FastAPI 기본 기능입니다.
- **Uvicorn** — Docker `CMD` 에서 `uvicorn app.main:app --workers 1 --no-access-log` 로 실행합니다. `python -m app.main` 도 내부에서 `uvicorn.run()` 을 부릅니다.
- **Pydantic v2** — 모든 요청/응답 모델(`VisionAnalysisRequest`, `DraftResponse` …)과 공통 조각(`app/common.py` 의 `VisionResult`, `PhotoInput`, `PromptSelectable`)을 정의합니다. `PhotoInput` 은 `model_validator` 로 `url` / `base64` 중 하나를 강제합니다. `app/cases.py` 의 `CaseStats` 는 C-5 집계 응답을 검증합니다.
- **pydantic-settings** — `app/config.py` 의 `Settings` 가 `.env` 와 환경변수를 읽습니다. 전체 목록은 [configuration.md](configuration.md).
- **python-multipart** — `POST /v1/vision/analyze-upload` 의 `UploadFile` / `Form` 처리에 필요합니다.

### Gemini (google-genai)

- `app/llm/gemini.py` — `genai.Client(api_key=…)` 로 `client.models.generate_content()` 를 부릅니다. 모든 LLM 노드가 `generate_structured()` 한 함수를 거칩니다.
  - `response_mime_type="application/json"` + `response_json_schema=<프롬프트 YAML 의 schema>` 로 **구조화 출력**을 받습니다.
  - `system_instruction`, `temperature`(프롬프트 YAML 값), `HttpOptions(timeout)`, `ThinkingConfig(thinking_level=…)`(`THINKING_LEVEL` 이 `off` 가 아닐 때).
  - ① 은 사진을 `types.Part.from_bytes()` 로 함께 보냅니다 (signed URL 이면 httpx 로 먼저 내려받음).
  - 재시도: JSON 파손·빈 응답·`429/500/502/503/504/UNAVAILABLE/RESOURCE_EXHAUSTED` 에 대해 `LLM_MAX_RETRIES` 회.
- `app/rag/embeddings.py` — `client.models.embed_content()` 를 `EmbedContentConfig(task_type, output_dimensionality=768)` 로 불러 문체 견본·질의를 임베딩합니다. 32개씩 배치, 결과는 단위 벡터로 정규화합니다.
- 모델 이름은 코드에 박혀 있지 않고 `MODEL_NAME` / `EMBEDDING_MODEL` 환경변수로 바꿀 수 있습니다.

### 임베딩 · RAG (자체 구현)

벡터 DB 나 numpy 없이 순수 파이썬으로 구현했습니다. 자세한 동작은 [style-rag.md](style-rag.md).

| 모듈 | 역할 |
|---|---|
| `app/rag/extract.py` | `pypdf` 로 PDF 를 읽어(지연 import — 런타임 이미지엔 pypdf 없음) 조각·역할 태깅·비식별 |
| `app/rag/profile.py` | 종결어미 분포 등 문체 통계 |
| `app/rag/embeddings.py` | Gemini 임베딩, 키 없이 쓰는 문자 3-gram **해시 임베딩** 대체물, 코사인 |
| `app/rag/store.py` | 색인 JSON(`app/rag/index/style_index.json`) 읽기·쓰기, `array` 기반 코사인 검색 |
| `app/rag/retriever.py` | 역할별 top-k 검색, 임베딩 → 해시 → 역할 순서 폴백, 60초 임베딩 휴지 |

부서 업무범위 검색(C-1)과 과거사례(C-5)는 **백엔드 쪽 RAG/SQL** 이고, stub 모드에서는 `app/backends/stub_client.py` 가 문자 bigram 자카드 유사도로 흉내 냅니다.

### Langfuse

- `app/prompts/langfuse_source.py` — `Langfuse(...)` 클라이언트 생성, `get_prompt()` 로 label(`vN`)별 프롬프트 조회, `scripts/sync_prompts.py` 의 업로드.
- `app/llm/tracing.py` — `start_as_current_observation(as_type="span"|"generation")`, `propagate_attributes(session_id, trace_name)`, 점수(`score`) 기록, `auth_check`, `flush`.
- `app/llm/masking.py` — 클라이언트의 `mask` 콜백으로 이메일·전화번호 등을 지웁니다.

자세한 내용은 [langfuse.md](langfuse.md).

### httpx

- `app/backends/http_client.py` — `BACKEND_MODE=http` 일 때 Spring 백엔드의 C-1/C-2/C-5 를 `httpx.post()` 로 부릅니다 (`X-Agent-Token` 헤더, `BACKEND_TIMEOUT_SECONDS`).
- `app/llm/gemini.py` — ① 의 `photo.url` 이 http(s) 면 사진을 내려받습니다 (15초 타임아웃, 리다이렉트 허용).
- `scripts/e2e.py` — 실행 중인 서버를 호출하는 테스트 클라이언트.

### PyYAML

- `app/prompts/__init__.py` — `defaults.yaml` 과 `<노드>/vN.yaml` 을 `yaml.safe_load` 로 읽습니다.

### 테스트

- `tests/test_draft_qa_reflection.py` — `unittest` + `unittest.mock.patch` 로 ④ 질의응답 반영 검증·보정 로직을 LLM 없이 검사합니다.
- 테스트 러너(pytest 등)는 requirements 에 들어 있지 않습니다.

### Docker · GitHub Actions · GHCR

- `Dockerfile` — `python:3.12-slim` 2단계 빌드, `/opt/venv`, `appuser`(uid 10001), `MALLOC_ARENA_MAX=2`, `/health` 헬스체크.
- `compose.yaml` — 로컬 단독 실행용 (`mem_limit: 160m`, `AI_BACKEND_MODE` 오버라이드).
- `.github/workflows/publish-image.yml` — `docker/login-action@v3`, `docker/metadata-action@v5`, `docker/build-push-action@v6` 로 `ghcr.io/anniversary-site/ai-server` 에 브랜치·태그·SHA 태그를 붙여 push.

자세한 내용은 [deployment.md](deployment.md).
