"""Gemini 구조화 출력 호출 래퍼.

Dify LLM 노드 하나 = 이 모듈의 generate_structured() 한 번.
노드 문서의 "구조화 출력 스키마" 를 그대로 response_json_schema 로 넘긴다.
"""

from __future__ import annotations

import base64
import json
import logging
import mimetypes
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
from google import genai
from google.genai import types

from app.config import get_settings
from app.llm import tracing

if TYPE_CHECKING:  # 런타임 순환 임포트를 피한다 (prompts 는 gemini 를 모른다)
    from app.prompts import Prompt

log = logging.getLogger(__name__)

_TRANSIENT_MARKERS = ("429", "500", "502", "503", "504", "UNAVAILABLE", "RESOURCE_EXHAUSTED")


class LlmError(RuntimeError):
    """LLM 호출 실패. 라우터에서 502 로 매핑된다."""


@dataclass(frozen=True)
class ImagePart:
    data: bytes
    mime_type: str


_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        settings = get_settings()
        if not settings.gemini_api_key:
            raise LlmError("GEMINI_API_KEY 가 비어 있다. ai/.env 를 확인할 것.")
        _client = genai.Client(api_key=settings.gemini_api_key)
    return _client


def load_image(source: str, *, timeout: float = 15.0) -> ImagePart:
    """사진 입력을 바이트로 만든다.

    백엔드(A-4)는 signed URL 을 주지만, 로컬 실험에서는 파일 경로나
    data: URI 로도 넣을 수 있어야 한다.
    """
    if source.startswith("data:"):
        header, _, payload = source.partition(",")
        mime = header[5:].split(";")[0] or "image/jpeg"
        return ImagePart(base64.b64decode(payload), mime)

    if source.startswith(("http://", "https://")):
        try:
            res = httpx.get(source, timeout=timeout, follow_redirects=True)
            res.raise_for_status()
        except httpx.HTTPError as exc:
            raise LlmError(f"사진을 내려받지 못했다: {source} ({exc})") from exc
        mime = res.headers.get("content-type", "image/jpeg").split(";")[0]
        return ImagePart(res.content, mime)

    path = Path(source)
    if not path.is_file():
        raise LlmError(f"사진 파일이 없다: {source}")
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    return ImagePart(path.read_bytes(), mime)


def load_image_from_base64(payload: str, mime_type: str = "image/jpeg") -> ImagePart:
    return ImagePart(base64.b64decode(payload), mime_type)


def generate_structured(
    *,
    node: str,
    system_prompt: str,
    user_prompt: str,
    schema: dict[str, Any],
    temperature: float,
    image: ImagePart | None = None,
    prompt: "Prompt | None" = None,
) -> dict[str, Any]:
    """LLM 노드 1회 실행. 스키마에 맞는 dict 를 돌려준다.

    ``prompt`` 는 추적용이다. 넘기면 Langfuse generation 에 프롬프트 이름·버전·소스가
    붙어 "어느 버전이 이 출력을 냈는지" 를 되짚을 수 있다. 없어도 동작은 같다.
    """
    settings = get_settings()
    client = _get_client()

    parts: list[types.Part] = []
    if image is not None:
        parts.append(types.Part.from_bytes(data=image.data, mime_type=image.mime_type))
    parts.append(types.Part.from_text(text=user_prompt))

    config = types.GenerateContentConfig(
        system_instruction=system_prompt,
        temperature=temperature,
        response_mime_type="application/json",
        response_json_schema=schema,
        http_options=types.HttpOptions(timeout=int(settings.llm_timeout_seconds * 1000)),
    )
    if settings.thinking_level and settings.thinking_level.lower() != "off":
        config.thinking_config = types.ThinkingConfig(thinking_level=settings.thinking_level)

    model_parameters: dict[str, Any] = {"temperature": temperature}
    if settings.thinking_level:
        model_parameters["thinking_level"] = settings.thinking_level

    # 재시도 전체를 generation 1개로 잡는다. 시도마다 나누면 같은 호출이 여러 건으로
    # 보여 프롬프트 버전별 집계가 어긋난다. 시도 횟수는 metadata 로 남긴다.
    with tracing.generation(
        name=node,
        model=settings.model_name,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        model_parameters=model_parameters,
        metadata=_trace_metadata(prompt, image),
        langfuse_prompt=getattr(prompt, "langfuse_prompt", None),
    ) as span:
        last_error: Exception | None = None
        for attempt in range(settings.llm_max_retries + 1):
            started = time.perf_counter()
            try:
                response = client.models.generate_content(
                    model=settings.model_name,
                    contents=[types.Content(role="user", parts=parts)],
                    config=config,
                )
                text = (response.text or "").strip()
                if not text:
                    raise ValueError("응답 본문이 비어 있다 (안전 필터 또는 토큰 초과 가능)")
                parsed = json.loads(text)
                log.info(
                    "llm node=%s model=%s attempt=%d elapsed=%.2fs",
                    node, settings.model_name, attempt + 1, time.perf_counter() - started,
                )
                span.finish(
                    output=parsed,
                    usage=tracing.usage_from_gemini(response),
                    metadata={"attempts": attempt + 1},
                )
                return parsed
            except Exception as exc:  # noqa: BLE001 - 재시도 판단 후 되던진다
                last_error = exc
                retryable = isinstance(exc, (json.JSONDecodeError, ValueError)) or any(
                    marker in str(exc) for marker in _TRANSIENT_MARKERS
                )
                log.warning(
                    "llm node=%s attempt=%d 실패(retryable=%s): %s",
                    node, attempt + 1, retryable, exc,
                )
                if not retryable or attempt == settings.llm_max_retries:
                    break
                time.sleep(1.5 * (attempt + 1))

        span.finish(metadata={"attempts": attempt + 1}, error=str(last_error))

    raise LlmError(f"[{node}] LLM 호출 실패: {last_error}") from last_error


def _trace_metadata(prompt: "Prompt | None", image: ImagePart | None) -> dict[str, Any]:
    """추적에 실을 부가 정보.

    프롬프트 식별자는 파일 모드에서도 남아야 한다 — 기본값이 파일 모드이고,
    A/B 비교는 대부분 거기서 한다.
    """
    meta: dict[str, Any] = {"has_image": image is not None}
    if prompt is not None:
        meta.update(
            prompt_name=prompt.name,
            prompt_version=prompt.version,
            prompt_source=prompt.source,
        )
        if prompt.langfuse_version:
            meta["langfuse_prompt_version"] = prompt.langfuse_version
    return meta
