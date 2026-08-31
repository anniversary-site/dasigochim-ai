"""① 사진 판독 — 01_vision_analysis.yml 대응.

Start → 초기 분석(LLM, 비전 켜짐) → End. 무상태.
"""

from __future__ import annotations

import base64
import uuid
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app import prompts
from app.common import PhotoInput, PromptSelectable, RiskLevel
from app.llm.gemini import generate_structured
from app.llm import tracing

NODE = "vision"
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # Gemini inline 첨부 한도(20MB)보다 넉넉히 아래로 잡는다.

NO_DESCRIPTION = "(제보자 설명 없음 — 사진 단독으로 판독할 것)"


class VisionAnalysisRequest(PromptSelectable):
    session_id: str
    location_text: str = Field(description="신고 위치 (백엔드 location.parse 정규화 결과)")
    description: str = Field(description="제보자 부연 설명")
    photo: PhotoInput


class VisionAnalysisResponse(BaseModel):
    detected_hazard: str
    photo_analysis: str
    risk: str
    exposed_target: str
    risk_level: RiskLevel
    object_confidence: float = Field(
        default=1.0, description="객체 식별 확신도. ② 가 이 값으로 객체 확인 질문 여부를 정한다"
    )
    object_candidates: list[str] = Field(
        default_factory=list, description="확신이 낮을 때의 객체 명칭 후보"
    )
    prompt_version: str = Field(default="", description="이번 판독에 쓰인 프롬프트 버전")


def run(req: VisionAnalysisRequest) -> VisionAnalysisResponse:
    """API 호출 1회 = Langfuse 트레이스 1개. session_id 로 신고 한 건이 묶인다."""
    with tracing.request_trace(req.session_id, "① 사진 판독"):
        result = _run(req)
        # 코드가 아는 사실을 점수로 남긴다. 프롬프트 버전별 확신도 분포를 Langfuse 에서
        # 바로 비교하려는 것이다 — v1·v2 는 이 필드가 없어 항상 1.0 으로 채워진다.
        tracing.score(
            "object_confidence", result.object_confidence, data_type="NUMERIC",
            comment=result.detected_hazard,
        )
        tracing.score("risk_level", result.risk_level, data_type="CATEGORICAL")
        return result


def _run(req: VisionAnalysisRequest) -> VisionAnalysisResponse:
    image = req.photo.load()
    prompt = prompts.pick(NODE, req.prompt_versions)
    # 설명이 비면 자리를 비워두지 않는다. 빈 칸을 두면 '판독의 기준입니다' 라는 머리말만 남아
    # 모델이 없는 설명을 지어내거나 지시를 무시한다. 규칙 4(사진 단독 판독)로 명시적으로 넘긴다.
    description = (req.description or "").strip() or NO_DESCRIPTION
    result = generate_structured(
        node=f"{prompt.node} [{prompt.label}]",
        system_prompt=prompt.system,
        user_prompt=prompt.render(
            location_text=req.location_text, description=description
        ),
        schema=prompt.schema,
        temperature=prompt.temperature,
        image=image,
        prompt=prompt,
    )
    # v1·v2 프롬프트에는 객체 확신도 필드가 없다. 그 경우 1.0(확신함)으로 두어
    # ② 의 객체 확인 질문 분기를 타지 않게 한다 — 옛 버전으로 A/B 할 때도 동작해야 한다.
    result.setdefault("object_confidence", 1.0)
    result.setdefault("object_candidates", [])
    return VisionAnalysisResponse(**result, prompt_version=prompt.version)


async def read_upload(file: UploadFile) -> PhotoInput:
    """업로드된 파일을 PhotoInput(base64) 으로 만든다. 형식·크기를 여기서 막는다."""
    mime = (file.content_type or "").split(";")[0].strip().lower()
    if not mime.startswith("image/"):
        raise HTTPException(
            status_code=415,
            detail=f"이미지 파일만 올릴 수 있다 (받은 형식: {file.content_type or '알 수 없음'})",
        )

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="빈 파일이다.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"사진이 너무 크다: {len(data) / 1_048_576:.1f}MB (한도 {MAX_UPLOAD_BYTES // 1_048_576}MB)",
        )

    return PhotoInput(base64=base64.b64encode(data).decode(), mime_type=mime)


router = APIRouter(tags=["① 사진 판독"])


@router.post("/v1/vision/analyze", response_model=VisionAnalysisResponse)
def analyze_photo(req: VisionAnalysisRequest) -> VisionAnalysisResponse:
    """현장 사진 1장 + 부연 설명 + 신고 위치를 받아 위험 요소를 판독한다.

    사진은 `photo.url`(signed URL / 로컬 경로 / data: URI) 또는 `photo.base64` 로 넘긴다.
    백엔드(A-4)가 쓰는 정식 경로다.
    """
    return run(req)


@router.post("/v1/vision/analyze-upload", response_model=VisionAnalysisResponse)
async def analyze_uploaded_photo(
    file: Annotated[UploadFile, File(description="현장 사진 파일 (jpg / png / webp, 10MB 이하)")],
    location_text: Annotated[str, Form(description="신고 위치")] = "김대건관 3층 서편 복도 (강의실 K308 앞)",
    description: Annotated[str, Form(description="제보자 부연 설명")] = "",
    session_id: Annotated[str, Form(description="비우면 자동 생성한다")] = "",
    prompt_version: Annotated[
        str, Form(description="프롬프트 버전 (v1 / v2 / v3 …). 비우면 기본 버전")
    ] = "",
) -> VisionAnalysisResponse:
    """`/v1/vision/analyze` 의 파일 업로드 판(multipart/form-data).

    Swagger UI(`/docs`)에서 로컬 사진을 바로 골라 시험하려고 둔 개발용 경로다.
    본문 스키마만 다르고 결과는 `/v1/vision/analyze` 와 동일하다.

    `prompt_version` 을 채우면 그 버전으로만 1회 판독한다 — 프롬프트 A/B 를
    서버 재시작 없이 `/docs` 에서 바로 돌려 보기 위한 것이다.
    """
    photo = await read_upload(file)
    return run(
        VisionAnalysisRequest(
            session_id=session_id.strip() or f"docs-{uuid.uuid4().hex[:8]}",
            location_text=location_text,
            description=description,
            photo=photo,
            prompt_versions={NODE: prompt_version.strip()} if prompt_version.strip() else None,
        )
    )
