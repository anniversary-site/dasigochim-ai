"""프롬프트 로더 — 프롬프트를 코드에서 떼어내 버전으로 관리한다.

LLM 노드 1개 = 디렉토리 1개 = 버전 파일 N개다.

    app/prompts/
      defaults.yaml          노드별 기본(활성) 버전
      vision/v1.yaml         ① Dify 원문
      vision/v2.yaml         ① 제보자 설명 우선
      vision/v3.yaml         ① + 객체 식별 허들
      question/v1.yaml       ② Dify 원문
      question/v2.yaml       ② 질문 축 로테이션
      object_check/v1.yaml   ② 객체 확인 질문 (신규 노드)
      ...

## 소스 두 가지

``PROMPT_SOURCE=file`` (기본) 은 위 YAML 을 읽는다. **git 이 진실이다.**
``PROMPT_SOURCE=langfuse`` 는 Langfuse 프롬프트 관리에서 읽고, 어떤 실패든
파일로 자동 폴백한다 (경고 로그 1줄). 시연 중 Langfuse 가 죽어도 서비스는 산다.

## 버전 선택 — 좁은 것이 이긴다

    1. 요청 본문 ``prompt_versions`` (노드별. A/B 용, 서버 재시작 불필요)
    2. ``.env`` 의 ``PROMPT_VERSIONS="vision=v2,question=v1"``
    3. ``defaults.yaml`` 의 ``active``

세 경로 모두 같은 식별자(`v1`, `v2`, …)를 쓴다. 파일 모드에서는 파일 이름이고,
Langfuse 모드에서는 label 이다 — ``scripts/sync_prompts.py`` 가 그렇게 올린다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.config import get_settings

log = logging.getLogger(__name__)

PROMPT_DIR = Path(__file__).resolve().parent
DEFAULTS_FILE = PROMPT_DIR / "defaults.yaml"

# 없으면 로드 시점에 터뜨린다. 프롬프트가 비거나 스키마가 빠진 채 LLM 을 부르면
# 502 만 남고 원인이 안 보인다.
REQUIRED_KEYS = ("node", "system", "user", "schema", "temperature")


class PromptError(RuntimeError):
    """프롬프트가 없거나 형식이 잘못됨. 설정 오류이므로 즉시 드러낸다."""


@dataclass(frozen=True)
class Prompt:
    """프롬프트 하나. ``generate_structured()`` 인자와 1:1 로 대응한다."""

    name: str
    version: str
    node: str
    system: str
    user: str
    schema: dict[str, Any]
    temperature: float
    changelog: str = ""
    source: str = "file"
    langfuse_version: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def render(self, **values: Any) -> str:
        """user 템플릿을 채운다.

        Langfuse 모드에서도 ``compile()`` 이 아니라 이 경로를 쓴다. 두 소스가 같은
        렌더링을 타야 "Langfuse 에서만 다르게 나온다" 는 버그가 생기지 않는다.
        """
        try:
            return self.user.format(**values)
        except KeyError as exc:
            raise PromptError(
                f"[{self.label}] user 템플릿의 자리표시자 {exc} 에 넘길 값이 없다."
            ) from exc

    def extra(self, key: str, default: str = "") -> str:
        """system/user 외의 부가 조각 (예: draft 의 regenerate 접미사)."""
        return str(self.raw.get(key, default))

    @property
    def label(self) -> str:
        tail = f"+lf{self.langfuse_version}" if self.langfuse_version else ""
        return f"{self.name}/{self.version}{tail}"


# ──────────────────────────────────────────────────────────────────────
# 버전 결정
# ──────────────────────────────────────────────────────────────────────

@lru_cache
def _defaults() -> dict[str, str]:
    if not DEFAULTS_FILE.is_file():
        raise PromptError(f"기본 버전 파일이 없다: {DEFAULTS_FILE}")
    data = yaml.safe_load(DEFAULTS_FILE.read_text(encoding="utf-8")) or {}
    return {str(k): str(v) for k, v in (data.get("active") or {}).items()}


@lru_cache
def _env_overrides() -> dict[str, str]:
    raw = (get_settings().prompt_versions or "").strip()
    if not raw:
        return {}
    out: dict[str, str] = {}
    for chunk in raw.split(","):
        if not chunk.strip():
            continue
        name, sep, version = chunk.partition("=")
        if not sep or not version.strip():
            raise PromptError(f"PROMPT_VERSIONS 형식 오류: {chunk!r} (노드=버전 형태)")
        out[name.strip()] = version.strip()
    return out


def available(name: str) -> list[str]:
    """해당 노드의 파일 버전들. v10 이 v9 뒤에 오도록 자릿수 우선 정렬한다."""
    directory = PROMPT_DIR / name
    if not directory.is_dir():
        return []
    return sorted((p.stem for p in directory.glob("v*.yaml")), key=lambda v: (len(v), v))


def resolve_version(name: str, override: str | None = None) -> str:
    if override:
        return override
    env = _env_overrides()
    if name in env:
        return env[name]
    defaults = _defaults()
    if name not in defaults:
        raise PromptError(f"defaults.yaml 의 active 에 '{name}' 이 없다.")
    return defaults[name]


# ──────────────────────────────────────────────────────────────────────
# 소스별 읽기
# ──────────────────────────────────────────────────────────────────────

def read_file(name: str, version: str) -> dict[str, Any]:
    """YAML 원본 dict. sync 스크립트도 이걸 쓴다."""
    path = PROMPT_DIR / name / f"{version}.yaml"
    if not path.is_file():
        raise PromptError(
            f"프롬프트 파일이 없다: {path} (있는 버전: {', '.join(available(name)) or '없음'})"
        )
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    missing = [k for k in REQUIRED_KEYS if k not in data]
    if missing:
        raise PromptError(f"{path} 에 필수 키가 없다: {missing}")
    return data


def _build(name: str, version: str, data: dict[str, Any], source: str) -> Prompt:
    return Prompt(
        name=name,
        version=version,
        node=str(data["node"]),
        system=str(data["system"]).strip(),
        user=str(data["user"]).strip(),
        schema=data["schema"],
        temperature=float(data["temperature"]),
        changelog=str(data.get("changelog", "")).strip(),
        source=source,
        langfuse_version=data.get("langfuse_version"),
        raw=data,
    )


@lru_cache
def _from_file(name: str, version: str) -> Prompt:
    return _build(name, version, read_file(name, version), "file")


def _from_langfuse(name: str, version: str) -> Prompt:
    from app.prompts import langfuse_source

    data = langfuse_source.fetch(name, version)
    return _build(name, version, data, "langfuse")


def load(name: str, version: str | None = None) -> Prompt:
    """노드 이름으로 프롬프트를 얻는다. version 을 주면 그 버전을 강제한다."""
    resolved = resolve_version(name, version)

    if get_settings().prompt_source.lower() == "langfuse":
        try:
            return _from_langfuse(name, resolved)
        except Exception as exc:  # noqa: BLE001 - 폴백이 목적이다
            log.warning(
                "langfuse 프롬프트 실패 → 파일로 폴백한다 (%s/%s): %s", name, resolved, exc
            )
    return _from_file(name, resolved)


def pick(name: str, overrides: dict[str, str] | None) -> Prompt:
    """요청 본문의 prompt_versions 를 반영해 로드한다. 라우터에서 쓰는 형태."""
    return load(name, (overrides or {}).get(name))


# ──────────────────────────────────────────────────────────────────────
# 목록 · 점검
# ──────────────────────────────────────────────────────────────────────

def node_names() -> list[str]:
    return sorted(
        p.name for p in PROMPT_DIR.iterdir()
        if p.is_dir() and not p.name.startswith(("_", "."))
    )


def catalog() -> list[dict[str, Any]]:
    """노드별 활성 버전과 선택 가능한 버전. GET /v1/prompts 응답."""
    out = []
    for name in node_names():
        versions = available(name)
        try:
            active = resolve_version(name)
        except PromptError as exc:
            active, note = "", str(exc)
        else:
            note = ""
        out.append({
            "name": name,
            "active": active,
            "available": versions,
            "source": get_settings().prompt_source,
            "changelog": {v: _from_file(name, v).changelog for v in versions},
            "note": note,
        })
    return out


def reload() -> None:
    """파일을 고친 뒤 캐시를 비운다 (실험용)."""
    _from_file.cache_clear()
    _defaults.cache_clear()
    _env_overrides.cache_clear()


def verify_all() -> None:
    """기동 시 1회. 활성 버전이 전부 읽히는지 확인해 오타를 즉시 드러낸다."""
    for name in node_names():
        if not available(name):
            raise PromptError(f"'{name}' 디렉토리에 버전 파일이 없다.")
        prompt = load(name)
        log.info(
            "prompt %-14s active=%-4s source=%-8s available=%s",
            name, prompt.version, prompt.source, ",".join(available(name)),
        )
