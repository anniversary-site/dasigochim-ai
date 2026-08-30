# syntax=docker/dockerfile:1
#
# 안전고래 AI 서버 (FastAPI + Gemini)
#
# 시연 배포 기준 — 2026-08-30_인프라_시연배포_최종결정.md
#   - t3.micro 1대에 nginx/backend/ai/postgres 를 나눠 띄운다
#   - AI 컨테이너는 mem_limit 160m, worker 1개
#   - 외부 포트 비공개 (compose 의 expose 만, ports 없음)
#   - EC2 에서 빌드하지 않는다. CI 가 GHCR 에 올린 이미지를 내려받아 쓴다
#
# 로컬 빌드:  docker build -t safewhale/ai:local .
# 로컬 실행:  docker compose up --build   (compose.yaml 참고)

# ── 1단계: 의존성만 별도로 설치한다 ────────────────────────────────────
# 빌드 도구와 pip 캐시를 최종 이미지에 남기지 않기 위해 venv 를 통째로 옮긴다.
FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build
COPY requirements.txt ./
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt


# ── 2단계: 실행 이미지 ─────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.title="safewhale-ai-server" \
      org.opencontainers.image.description="안전고래 AI 서버 — 사진 판독·질문 생성·부서 판정·신고서 초안·인사이트" \
      org.opencontainers.image.source="https://github.com/safewhaleartechsogang/ai-server"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    # glibc 가 스레드마다 malloc arena 를 잡아 RSS 가 부는 것을 막는다 (160m 예산).
    MALLOC_ARENA_MAX=2 \
    # app/config.py 기본값이 127.0.0.1 이라 컨테이너에서는 반드시 덮어써야 한다.
    HOST=0.0.0.0 \
    PORT=8000

RUN useradd --create-home --uid 10001 appuser

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
# app/ 만 넣는다. scripts/(e2e·샘플 생성)와 문서는 실행에 필요 없다 — .dockerignore 참고.
# app/backends/fixtures/ 는 BACKEND_MODE=stub 에서 쓰이므로 함께 들어간다.
COPY --chown=appuser:appuser app/ ./app/

USER appuser

EXPOSE 8000

# python -c 한 줄로 확인한다. slim 이미지에 curl/wget 이 없고,
# 헬스체크마다 프로세스가 하나 더 뜨므로 간격을 30s 로 잡아 메모리 스파이크를 줄인다.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"]

# worker 1개 고정 (인프라 문서 기준). 요청 로깅은 app/main.py 의 미들웨어가 이미 하므로
# uvicorn 접근 로그는 꺼서 중복을 없앤다.
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "1", \
     "--no-access-log"]
