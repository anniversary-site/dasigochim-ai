"""5개 API 가 공유하는 입력 조각과 조립 헬퍼."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.llm.gemini import ImagePart, load_image, load_image_from_base64

RiskLevel = Literal["low", "medium", "high", "critical"]

NO_QA = "(아직 없음)"
NO_QA_FINAL = "(없음)"


class VisionResult(BaseModel):
    """① 사진 판독 결과. ②③④⑤ 가 공통으로 받는다."""

    detected_hazard: str
    photo_analysis: str
    risk: str
    exposed_target: str


class QaPair(BaseModel):
    question: str
    answer: str


class PhotoInput(BaseModel):
    """사진 입력. 백엔드는 signed URL 을, 로컬 실험은 파일 경로/base64 를 쓴다."""

    url: str | None = Field(default=None, description="signed URL, 로컬 경로, 또는 data: URI")
    base64: str | None = Field(default=None, description="base64 인코딩된 이미지 바이트")
    mime_type: str = "image/jpeg"

    @model_validator(mode="after")
    def _one_of(self) -> "PhotoInput":
        if not self.url and not self.base64:
            raise ValueError("photo.url 또는 photo.base64 중 하나는 있어야 한다.")
        return self

    def load(self) -> ImagePart:
        if self.base64:
            return load_image_from_base64(self.base64, self.mime_type)
        return load_image(self.url or "")


def format_qa_history(
    qa_history: str | None,
    qa_pairs: list[QaPair] | None,
    *,
    empty: str = NO_QA,
) -> str:
    """qa_history 를 프롬프트에 넣을 한 덩어리 텍스트로 만든다.

    백엔드는 이미 정리된 문자열을 주지만, 로컬 실험에서 구조체로 넣는 쪽이
    편해서 둘 다 받는다. 문자열이 있으면 그쪽을 우선한다.
    """
    if qa_history and qa_history.strip():
        return qa_history.strip()
    if qa_pairs:
        return "\n".join(f"Q{i}. {p.question}\nA{i}. {p.answer}" for i, p in enumerate(qa_pairs, 1))
    return empty


def join_query(*parts: Any) -> str:
    """검색 질의 텍스트 조립 (Dify '검색 요청 조립' code 노드와 동일)."""
    return " ".join(str(p).strip() for p in parts if p and str(p).strip())
