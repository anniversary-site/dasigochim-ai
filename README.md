# 다시고침 AI 서버

**다시고침**은 서울시 청년안심주택 입주민을 위한 시설 민원 서비스입니다.
입주민이 사진을 찍으면 AI 챗봇이 몇 가지를 되묻고 신고서 초안을 써 주고, 입주민은 3D 평면도에 위치 핀을 꽂아 제출합니다.
관리자는 대시보드 · 3D 지도 · 챗봇으로 민원을 살펴봅니다.

이 저장소는 그중 **AI 서버**입니다. FastAPI + Google Gemini 로 만들었고,
사진 판독 → 확인 질문 → 담당 부서 판정 → 신고서 초안·위험도 → 요약·인사이트까지 5단계를 맡습니다.

> 원래 Dify 로 만들었던 앱 5개를 Dify 없이 옮긴 것입니다. **Dify 앱 1개 = REST 엔드포인트 1개 = 파이썬 모듈 1개**(`app/apps/a1..a5`).

데모: <https://dasigochim-frontend.vercel.app>

---

## 이 저장소의 역할

- **Spring 백엔드만 이 서버를 부릅니다.** 프론트는 AI 서버를 직접 호출하지 않습니다. 운영에서는 외부 포트를 열지 않습니다.
- **무상태입니다.** 세션을 들고 있지 않고, 맥락(판독 결과, 질의응답)은 백엔드가 매번 넘깁니다.
- ③ ④ ⑤ 는 부서 업무범위·과거 신고 데이터가 필요해서 **백엔드 내부 API(C-1/C-2/C-5)를 거꾸로 호출**합니다. 백엔드 없이 개발할 때는 로컬 픽스처(`stub` 모드)로 대신합니다.
- 프롬프트는 코드가 아니라 `app/prompts/<노드>/vN.yaml` 에 버전별로 있습니다.

```
 ┌──────────────┐        ┌──────────────────┐                ┌───────────────────────────┐
 │   프론트엔드   │ ─────▶ │   Spring 백엔드    │ ── HTTP ─────▶ │   AI 서버 (이 저장소)         │
 └──────────────┘        │                  │                │                           │
                         │  질문 단계         │  ① vision      │  사진 판독 (Gemini 비전)       │
                         │                  │  ② question×3  │  확인 질문 + 선택지 3개         │
                         │  초안 단계         │  ④ draft/risk  │  위험도 재산출 + 신고서 초안     │
                         │                  │  ③ department  │  담당 부서 판정               │
                         │  제출 후 (비동기)   │  ⑤ insight     │  요약 · 반복 발생 인사이트       │
                         │                  │                │                           │
                         │  /internal/v1/*  │ ◀── C-1/C-2 ── │  ③ 업무범위 검색 · 부서 확정     │
                         │  (X-Agent-Token) │ ◀── C-5 ────── │  ④⑤ 과거사례 · 반복 집계        │
                         └──────────────────┘                └──────────────┬────────────┘
                                                                            │
                                                              Google Gemini · Langfuse(선택)
```

백엔드는 초안 단계에서 ④ 를 먼저, ③ 을 그다음에 부릅니다. ① 결과는 백엔드가 캐시해 재사용합니다.
노드별 자세한 입출력은 [readme/pipeline.md](readme/pipeline.md) 에 있습니다.

---

## 엔드포인트

| 단계 | 엔드포인트 | 하는 일 | 모듈 |
|---|---|---|---|
| ① | `POST /v1/vision/analyze` | 사진 + 설명 + 위치 → 위험 요소 판독, 잠정 위험 등급 | `app/apps/a1_vision.py` |
| ① | `POST /v1/vision/analyze-upload` | 위와 같음, 파일 업로드판 (`/docs` 시험용) | `app/apps/a1_vision.py` |
| ② | `POST /v1/questions/next` | 확인 질문 1문 + 선택지 3개 | `app/apps/a2_question.py` |
| ③ | `POST /v1/departments/classify` | 담당 부서 판정 (C-1 · C-2 호출) | `app/apps/a3_department.py` |
| ④ | `POST /v1/drafts/generate` | 위험도 재산출 + 신고서 초안 (C-5 · 문체 RAG) | `app/apps/a4_draft.py` |
| ⑤ | `POST /v1/insights/generate` | 요약 · 키워드 · 반복 발생 인사이트 (C-5) | `app/apps/a5_insight.py` |

| 운영·점검용 | 하는 일 |
|---|---|
| `GET /health` | 모델 · 백엔드 모드 · 키 로드 · Langfuse · 문체 RAG 상태 |
| `GET /v1/prompts` · `GET /v1/prompts/{노드}/{버전}` · `POST /v1/prompts/reload` | 프롬프트 버전 조회 · 전문 보기 · 캐시 비우기 |
| `GET /v1/rag/status` · `GET /v1/rag/style` · `POST /v1/rag/reload` | 문체 색인 상태 · 실제 견본 블록 · 캐시 비우기 |

LLM 이나 백엔드 호출이 실패하면 `502` 와 `{"error": "llm_failed" | "backend_failed", ...}` 를 돌려줍니다.
요청·응답 필드 상세는 [API_명세서.md](API_명세서.md) 와 실행 중인 서버의 Swagger UI(`/docs`)를 보세요.

현재 기본 프롬프트는 **vision v4 · question v3 · risk v2 · draft v3** 입니다.
vision v4 와 risk v2 는 고용노동부 위험성평가 지침 · KOSHA GUIDE H-205-2018 을 준용한 **가능성 × 중대성** 3×3 매트릭스로 `low` / `medium` / `high` 를 판정합니다.

---

## 빠른 시작

### 1) 환경변수

```bash
cp .env.example .env
```

| 키 | 기본값 | 메모 |
|---|---|---|
| `GEMINI_API_KEY` | — | **필수** |
| `MODEL_NAME` | `gemini-3.1-flash-lite` | |
| `BACKEND_MODE` | `stub` | `stub` = 로컬 픽스처 / `http` = 실제 백엔드 |
| `BACKEND_BASE_URL` | `http://localhost:8080` | `http` 모드에서 사용 |
| `AGENT_TOKEN` | `dev-agent-token` | 백엔드 내부 API 호출 헤더. 운영에서는 반드시 변경 |
| `PROMPT_SOURCE` | `file` | `file` / `langfuse` |
| `LANGFUSE_PUBLIC_KEY` · `LANGFUSE_SECRET_KEY` | — | 채우면 추적이 켜짐 (선택) |

> `.env.example` 은 `BACKEND_MODE=http` 로 적혀 있습니다. 백엔드 없이 혼자 돌려 보려면 `stub` 으로 바꿔 주세요.
> 전체 목록은 [readme/configuration.md](readme/configuration.md).

### 2-a) Docker Compose

```bash
docker compose up --build                       # stub 모드 → http://127.0.0.1:8000/docs
AI_BACKEND_MODE=http docker compose up --build  # 호스트의 Spring 백엔드(8080)와 연결
```

### 2-b) uvicorn

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

다른 터미널에서 전체 플로우(① → ②×3 → ③ → ④ → ④ 재생성 → ⑤)를 한 번에 돌려 볼 수 있습니다.

```bash
python scripts/e2e.py --photo ./현장.jpg
```

### stub 모드 vs http 모드

| | `stub` | `http` |
|---|---|---|
| 데이터 | `app/backends/fixtures/*.json` | Spring 백엔드 `/internal/v1/*` |
| 언제 | AI 서버만 개발·시험할 때 | 통합 실행, 운영 (`compose.prod.yaml` 은 `http`) |

C-1/C-2/C-5 계약은 [readme/configuration.md](readme/configuration.md#백엔드-내부-계약-c-1--c-2--c-5) 를 보세요.

---

## 문서

| 문서 | 내용 |
|---|---|
| [readme/pipeline.md](readme/pipeline.md) | ①~⑤ 노드별 입력·출력·프롬프트·백엔드 호출 순서, 위험도 매트릭스, **프롬프트 버전 관리**, Dify 와 달라진 점 |
| [readme/configuration.md](readme/configuration.md) | **환경변수 전체**, `BACKEND_MODE` stub/http, 백엔드 내부 계약 C-1/C-2/C-5 |
| [readme/tech-stack.md](readme/tech-stack.md) | 기술 스택과 버전, 각각을 어느 모듈에서 쓰는지 |
| [readme/style-rag.md](readme/style-rag.md) | ④ 문체 RAG — 재해조사의견서 색인, 폴백, 점검 API |
| [readme/langfuse.md](readme/langfuse.md) | Langfuse 프롬프트 소스·동기화, 추적, 개인정보 마스킹 |
| [readme/deployment.md](readme/deployment.md) | Docker 이미지 구성, compose, GHCR 발행, 실배포 |
| [readme/development.md](readme/development.md) | 로컬 실행, e2e 러너, `/docs` 업로드 시험, 테스트, 디렉터리 구조 |
| [API_명세서.md](API_명세서.md) | 엔드포인트별 요청·응답 상세 (코드와 어긋나면 코드가 맞습니다) |
| [노드별설명.md](노드별설명.md) | 원본 Dify 앱의 노드 배선과 원문 프롬프트 |
| [docs/](docs/) | 고용노동부 재해조사의견서 12건 (④ 문체 RAG 원본 PDF) |

---

## 알려진 한계

- **인바운드 인증 없음.** AI 서버는 들어오는 요청의 `X-Agent-Token` 을 검증하지 않습니다. 그래서 운영에서는 포트를 외부에 열지 않고 백엔드만 내부망으로 부릅니다.
- **멱등성 없음.** 같은 `session_id` 로 두 번 부르면 LLM 도 두 번 호출됩니다. 중복 방지는 백엔드 몫입니다.
- `session_id` 는 로깅·추적용입니다. 서버는 세션을 저장하지 않습니다.
- 부서 판정 임계값(0.7)과 `top_k`(③ 8 / ④⑤ 4)는 문서 기본값 그대로입니다. 실측 후 조정이 필요합니다.

---

## 관련 저장소

| 저장소 | 역할 |
|---|---|
| [dasigochim-frontend](https://github.com/anniversary-site/dasigochim-frontend) | 입주민 · 관리자 웹 (<https://dasigochim-frontend.vercel.app>) |
| [dasigochim-backend](https://github.com/anniversary-site/dasigochim-backend) | Spring Boot API — 이 AI 서버를 호출하고 C-1/C-2/C-5 를 제공 |
| **dasigochim-ai** (여기) | FastAPI + Gemini AI 서버 |
| [dasigochim-deployment](https://github.com/anniversary-site/dasigochim-deployment) | EC2 위 Docker Compose 배포 (`compose.prod.yaml`) |
