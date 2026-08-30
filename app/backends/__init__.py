"""백엔드 클라이언트 선택. BACKEND_MODE 로 stub / http 를 바꿔 끼운다."""

from __future__ import annotations

from functools import lru_cache

from app.backends.base import BackendClient, BackendError
from app.config import get_settings

__all__ = ["BackendClient", "BackendError", "get_backend"]


@lru_cache
def get_backend() -> BackendClient:
    mode = get_settings().backend_mode.lower()
    if mode == "http":
        from app.backends.http_client import HttpBackendClient

        return HttpBackendClient()
    if mode == "stub":
        from app.backends.stub_client import StubBackendClient

        return StubBackendClient()
    raise BackendError(f"알 수 없는 BACKEND_MODE: {mode} (stub | http)")
