# 컨테이너와 배포

[← README 로 돌아가기](../README.md) · 관련: [configuration.md](configuration.md) · [tech-stack.md](tech-stack.md)

역할 분담은 이렇습니다.

- **이 저장소** — `Dockerfile` 을 관리하고, `main` 브랜치·`v*` 태그에 push 되면 GitHub Actions 가 이미지를 GHCR 에 올립니다.
- **`compose.yaml`** (이 저장소) — AI 서버 하나만 띄워 보는 **개발 편의용**입니다.
- **[dasigochim-deployment](https://github.com/anniversary-site/dasigochim-deployment)** — 실배포 기준입니다. `compose.prod.yaml` 이 GHCR 이미지를 내려받아 EC2 에서 nginx · backend · ai · postgres 를 함께 띄웁니다.

> **EC2 에서 빌드하지 않습니다.** CI 가 GHCR 에 이미지를 올리고 EC2 는 태그로 내려받습니다.

---

## 로컬에서 컨테이너로 띄우기

```bash
cp .env.example .env              # GEMINI_API_KEY 채우기
docker compose up --build         # stub 모드 → http://127.0.0.1:8000/docs
```

호스트에서 도는 Spring 백엔드와 붙여 보려면:

```bash
AI_BACKEND_MODE=http docker compose up --build
# PowerShell:  $env:AI_BACKEND_MODE = "http"; docker compose up --build
```

`compose.yaml` 이 하는 일:

- `.env` 를 `env_file` 로 읽고, `HOST=0.0.0.0` · `PORT=8000` · `BACKEND_MODE=${AI_BACKEND_MODE:-stub}` · `BACKEND_BASE_URL=${AI_BACKEND_BASE_URL:-http://host.docker.internal:8080}` 로 덮어씁니다.
- `AI_PORT`(기본 8000)로 포트를 엽니다 (개발용. 실배포는 `ports` 없이 `expose` 만).
- Linux 에서도 `host.docker.internal` 이 되도록 `extra_hosts: host-gateway` 를 둡니다.
- `mem_limit: 160m` — 원래 t3.micro(1GiB) 예산 기준으로 맞춘 값입니다. 여기서 OOM 이 나면 서버에서도 납니다.

`AI_` 접두사를 쓰는 이유는 [configuration.md](configuration.md#composeyaml-전용-오버라이드) 를 보세요.

---

## 이미지 구성 (`Dockerfile`)

| 항목 | 값 | 이유 |
|---|---|---|
| 베이스 | `python:3.12-slim`, 2단계 빌드 | 1단계에서 `/opt/venv` 에 의존성을 설치하고 venv 만 옮겨, 빌드 도구·pip 캐시를 최종 이미지에서 뺍니다 |
| 들어가는 것 | `app/` 과 `requirements.txt` 의 런타임 의존성뿐 | `scripts/` · 문서(`*.md`) · `docs/` PDF · 테스트 사진 · `.env` 는 `.dockerignore` 로 제외 |
| 함께 들어가는 데이터 | `app/backends/fixtures/`, `app/rag/index/`, `app/prompts/` | stub 모드·문체 RAG·프롬프트가 런타임에 읽습니다 |
| worker | **1개 고정** (`uvicorn --workers 1`) | 메모리 예산 |
| 접근 로그 | `--no-access-log` | `app/main.py` 의 요청 로깅 미들웨어와 중복을 없앰 |
| 포트 | `EXPOSE 8000` | 실배포는 `expose` 만, 외부 비공개. 백엔드만 `http://ai:8000` 으로 호출 |
| 실행 사용자 | `appuser` (uid 10001) | root 로 돌리지 않습니다 |
| 헬스체크 | `python -c urllib…` 로 `/health`, 30초 간격 | slim 에 curl/wget 이 없습니다. 간격을 늘려 메모리 스파이크를 줄였습니다 |
| `MALLOC_ARENA_MAX=2` | glibc arena 축소 | 작은 메모리 예산에서 RSS 가 부는 것을 막습니다 |
| `HOST=0.0.0.0` | 환경변수 | `app/config.py` 기본값이 `127.0.0.1` 이라 컨테이너에서는 덮어써야 합니다 |

`pillow` 와 `pypdf` 는 스크립트 전용이라 런타임 의존성(`requirements.txt`)에서 뺐습니다. 로컬 개발은 `requirements-dev.txt` 를 씁니다.

```bash
docker build -t dasigochim/ai:local .
```

---

## GHCR 이미지 발행 (`.github/workflows/publish-image.yml`)

| 항목 | 값 |
|---|---|
| 트리거 | `main` 브랜치 push, `v*` 태그 push, 수동 실행(`workflow_dispatch`) |
| 이미지 | `ghcr.io/<저장소 소유자>/ai-server` → `ghcr.io/anniversary-site/ai-server` |
| 태그 | 브랜치명(`main`), git 태그(`v…`), 커밋 SHA |
| 사용 액션 | `actions/checkout@v4`, `docker/login-action@v3`, `docker/metadata-action@v5`, `docker/build-push-action@v6` |
| 권한 | `contents: read`, `packages: write` (`GITHUB_TOKEN`) |

---

## 실배포 Compose

실제 값은 dasigochim-deployment 저장소의 `compose.prod.yaml` 이 기준입니다. 그 파일의 `ai` 서비스 요지는 다음과 같습니다.

- `image: ${AI_IMAGE}` (`pull_policy: always`), `restart: unless-stopped`
- `BACKEND_MODE=http`, `BACKEND_BASE_URL=http://backend:8080`
- `AGENT_TOKEN`, `GEMINI_API_KEY` 필수, `MODEL_NAME` · `THINKING_LEVEL` · `LANGFUSE_*` 는 기본값과 함께 주입 (`LANGFUSE_TRACING_ENVIRONMENT` 기본 `prod`)
- `expose: "8000"` (ports 없음), `mem_limit: ${AI_MEMORY_LIMIT:-256m}`
- `backend` 가 healthy 가 된 뒤 기동, 볼륨 없음 (AI 서버는 무상태)

백엔드 쪽에는 AI 서버 주소(`http://ai:8000`)를 설정합니다. 배포 절차 전체는 dasigochim-deployment 의 문서를 보세요.

> 참고: `Dockerfile` · `compose.yaml` 주석은 초기 인프라 결정(t3.micro, `mem_limit 160m`)을 기준으로 적혀 있고,
> 현재 운영 compose 의 기본 메모리 한도는 `256m` 입니다.
