"""Langfuse 프롬프트 관리 연동.

**git 의 YAML 이 진실이고, Langfuse 는 그 사본을 운영·실험용으로 굴리는 층이다.**
Langfuse 가 죽어도 서비스가 죽으면 안 되므로 어떤 실패든 파일 소스로 되돌아간다.

- 노드 하나 = Langfuse 프롬프트 하나 (`safewhale/vision` 처럼 prefix 를 붙인다)
- system/user 는 chat 프롬프트의 두 메시지로 올린다 (Langfuse UI 에서 그대로 읽힌다)
- temperature 와 JSON 스키마는 `config` 에 실린다 — 버전과 함께 고정되어야 하기 때문이다
- 로컬 파일 버전 `vN` 은 Langfuse **label** 로 올라간다. 그래서 파일 모드와 Langfuse
  모드가 `vN` 이라는 같은 식별자를 쓴다 (`resolve_version()` 결과를 그대로 넘길 수 있다)

변수 치환은 Langfuse 의 ``compile()`` (mustache ``{{var}}``) 을 쓰지 않고 파일 모드와
똑같이 파이썬 ``str.format()`` (``{var}``) 으로 한다. 두 모드가 같은 렌더링 경로를 타야
"Langfuse 에서는 다르게 나온다" 는 종류의 버그가 안 생긴다. 대신 Langfuse UI 의 변수
자동 인식 기능은 쓰지 못한다 — 의도한 맞바꿈이다.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

from app.config import get_settings

log = logging.getLogger(__name__)

PRODUCTION_LABEL = "production"
# Prompt.raw 에 실어 나르는 SDK 프롬프트 객체의 키. 밑줄로 시작해 YAML 키와 겹치지 않는다.
CLIENT_PROMPT_KEY = "_langfuse_prompt_object"


class LangfuseUnavailable(RuntimeError):
    """Langfuse 를 쓸 수 없음. 호출부는 파일 소스로 폴백한다."""


@lru_cache
def get_langfuse():
    """Langfuse 클라이언트. 키가 없으면 만들지 않는다."""
    settings = get_settings()
    if not settings.langfuse_ready:
        raise LangfuseUnavailable(
            "LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY 가 비어 있다. .env 를 확인할 것."
        )
    try:
        from langfuse import Langfuse
    except ImportError as exc:  # requirements 에 있으나 방어한다
        raise LangfuseUnavailable(f"langfuse 패키지를 불러오지 못했다: {exc}") from exc

    return Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        base_url=settings.langfuse_base_url,
        tracing_enabled=settings.langfuse_tracing,
    )


def prompt_name(node: str) -> str:
    prefix = get_settings().langfuse_prompt_prefix.strip()
    return f"{prefix}/{node}" if prefix else node


def fetch(node: str, version: str) -> dict[str, Any]:
    """Langfuse 에서 한 노드의 프롬프트를 가져온다.

    ``version`` 은 label 로 해석한다 ('v3', 'production', 'latest').
    반환 형태는 파일 YAML 과 동일한 dict 라 Prompt 조립 경로를 공유한다.
    """
    settings = get_settings()
    client = get_langfuse()
    name = prompt_name(node)

    try:
        fetched = client.get_prompt(
            name,
            label=version,
            type="chat",
            cache_ttl_seconds=settings.prompt_cache_ttl_seconds,
        )
    except Exception as exc:  # noqa: BLE001 - 어떤 실패든 파일로 폴백시킨다
        raise LangfuseUnavailable(f"{name}@{version} 조회 실패: {exc}") from exc

    messages = list(getattr(fetched, "prompt", None) or [])
    system = _message(messages, "system")
    user = _message(messages, "user")
    config = dict(getattr(fetched, "config", None) or {})

    if not system or not user:
        raise LangfuseUnavailable(f"{name}@{version} 에 system/user 메시지가 없다.")
    if "schema" not in config or "temperature" not in config:
        raise LangfuseUnavailable(
            f"{name}@{version} 의 config 에 schema/temperature 가 없다. "
            "scripts/sync_prompts.py 로 다시 올릴 것."
        )

    return {
        "node": config.get("node", node),
        "system": system,
        "user": user,
        "schema": config["schema"],
        "temperature": config["temperature"],
        "changelog": config.get("changelog", ""),
        "langfuse_version": getattr(fetched, "version", None),
        # 추적에서 generation 에 이 프롬프트를 링크하려면 SDK 객체 자체가 필요하다.
        # (app/llm/tracing.py 가 Prompt.langfuse_prompt 로 꺼내 쓴다)
        CLIENT_PROMPT_KEY: fetched,
        **{k: v for k, v in config.items()
           if k not in ("schema", "temperature", "node", "changelog")},
    }


def _message(messages: list[Any], role: str) -> str:
    for m in messages:
        got = m.get("role") if isinstance(m, dict) else getattr(m, "role", None)
        if got == role:
            content = m.get("content") if isinstance(m, dict) else getattr(m, "content", "")
            return str(content or "").strip()
    return ""


def push(node: str, version: str, data: dict[str, Any], *, production: bool) -> Any:
    """로컬 YAML 한 개를 Langfuse 로 올린다. scripts/sync_prompts.py 가 쓴다.

    labels 에 파일 버전(`vN`)을 넣어 파일과 Langfuse 가 같은 식별자를 쓰게 하고,
    defaults.yaml 의 활성 버전이면 `production` 도 함께 단다.
    """
    client = get_langfuse()
    labels = [version] + ([PRODUCTION_LABEL] if production else [])
    config = {
        "node": data["node"],
        "temperature": data["temperature"],
        "schema": data["schema"],
        "changelog": data.get("changelog", ""),
        "file_version": version,
    }
    for key in ("regenerate_system_suffix", "regenerate_user_suffix", "regenerate_node"):
        if key in data:
            config[key] = data[key]

    return client.create_prompt(
        name=prompt_name(node),
        type="chat",
        prompt=[
            {"role": "system", "content": str(data["system"]).strip()},
            {"role": "user", "content": str(data["user"]).strip()},
        ],
        labels=labels,
        tags=["safewhale", node],
        config=config,
        commit_message=(data.get("changelog") or f"{node} {version}").strip().splitlines()[0][:200],
    )
