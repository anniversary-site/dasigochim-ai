"""① 사진 판독 — 01_vision_analysis.yml 대응.

Start → 초기 분석(LLM, 비전 켜짐) → End. 무상태.
"""

from __future__ import annotations

import base64
import uuid
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app.common import PhotoInput, RiskLevel
from app.llm.gemini import generate_structured

TEMPERATURE = 0.2
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # Gemini inline 첨부 한도(20MB)보다 넉넉히 아래로 잡는다.

SYSTEM_PROMPT = """당신은 대학 안전·보건 담당자를 보조하는 위해 요소 분석가입니다.
제보자가 올린 현장 사진 한 장과 부연 설명, 그리고 신고 위치를 받아 위험 요소를 판독합니다.

[작성 규칙]
1. detected_hazard: 사진에서 확인되는 위험 요소를 짧은 명사구로 특정합니다. (예: "바닥 타일 파손 (모서리 돌출)")
2. photo_analysis: 사진에서 실제로 보이는 상태만 객관적으로 기술합니다. 크기·개수·정도를 알 수 있으면 포함합니다.
   사진에서 확인되지 않는 내용을 추측해 단정하지 않습니다.
3. risk: 해당 상태가 유발할 수 있는 사고를 인과관계로 서술합니다.
4. exposed_target: 그 위험에 노출될 수 있는 사람을 신고 위치의 성격에 맞게 특정합니다.
5. risk_level: low / medium / high / critical 중 하나를 고릅니다.
   - critical: 즉시 인명 사고로 이어질 수 있음
   - high: 중상 가능성이 있음
   - medium: 경상·전도 등 부상 가능성이 있음
   - low: 즉각적 위험은 낮으나 개선이 필요함
6. 제보자의 감정 표현은 제거하고 사실만 남깁니다.
7. 모든 문장은 한국어 문어체로 작성합니다.
8. 반드시 주어진 JSON 스키마 형식으로만 출력합니다."""

USER_PROMPT = """[사진]
(첨부됨)

[신고 위치]
{location_text}

[제보자 부연 설명]
{description}

위 위치에서 촬영된 첨부 사진의 위해 요소를 판독하세요."""

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "detected_hazard": {"type": "string", "description": "감지된 위험 요소 (짧은 명사구)"},
        "photo_analysis": {"type": "string", "description": "사진에서 관찰되는 상태"},
        "risk": {"type": "string", "description": "위험성 (인과관계 서술)"},
        "exposed_target": {"type": "string", "description": "위험에 노출될 수 있는 대상"},
        "risk_level": {
            "type": "string",
            "description": "위험 등급",
            "enum": ["low", "medium", "high", "critical"],
        },
    },
    "required": [
        "detected_hazard",
        "photo_analysis",
        "risk",
        "exposed_target",
        "risk_level",
    ],
}


class VisionAnalysisRequest(BaseModel):
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


def run(req: VisionAnalysisRequest) -> VisionAnalysisResponse:
    image = req.photo.load()
    result = generate_structured(
        node="1801000002 초기 분석",
        system_prompt=SYSTEM_PROMPT,
        user_prompt=USER_PROMPT.format(
            location_text=req.location_text, description=req.description
        ),
        schema=SCHEMA,
        temperature=TEMPERATURE,
        image=image,
    )
    return VisionAnalysisResponse(**result)


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
) -> VisionAnalysisResponse:
    """`/v1/vision/analyze` 의 파일 업로드 판(multipart/form-data).

    Swagger UI(`/docs`)에서 로컬 사진을 바로 골라 시험하려고 둔 개발용 경로다.
    본문 스키마만 다르고 결과는 `/v1/vision/analyze` 와 동일하다.
    """
    photo = await read_upload(file)
    return run(
        VisionAnalysisRequest(
            session_id=session_id.strip() or f"docs-{uuid.uuid4().hex[:8]}",
            location_text=location_text,
            description=description,
            photo=photo,
        )
    )
