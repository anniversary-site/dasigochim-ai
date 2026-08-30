"""② 확인 질문 생성 — 02_question_gen.yml 대응.

Start → 질문생성기(LLM) → 선택지 3개 보정(Code) → End. 무상태.
백엔드가 question_index 1·2·3 으로 3회 호출한다.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.common import NO_QA, QaPair, VisionResult, format_qa_history
from app.llm.gemini import generate_structured

TEMPERATURE = 0.7
UNKNOWN_LABEL = "잘 모르겠어요"

SYSTEM_PROMPT = """당신은 안전 신고서를 작성하기 위해 제보자에게 확인 질문을 던지는 담당자입니다.
사진 판독 결과와 지금까지의 질의응답을 보고, 신고서를 더 정확하게 쓰기 위해
아직 확인되지 않은 정보를 묻는 질문 하나를 만듭니다.

[작성 규칙]
1. 질문은 제보자가 실제로 알 수 있는 내용만 묻습니다.
   (예: 언제부터 그랬는지, 누가 그 구역을 쓰는지, 임시 조치가 되어 있는지, 재발 여부)
   제보자가 알 수 없는 전문적 판단(구조 안전성 수치 등)은 묻지 않습니다.
2. 이미 받은 답변으로 알 수 있는 내용은 절대 다시 묻지 않습니다.
3. options에는 서로 겹치지 않는 선택지를 정확히 3개 제시합니다.
   - 제보자가 고르기 쉬운 짧은 문장으로 씁니다.
   - "잘 모르겠어요"처럼 회피 가능한 선택지를 하나 포함해도 좋습니다.
   - value는 영문 소문자 스네이크케이스 식별자, label은 사용자에게 보일 한국어 문장입니다.
4. "직접 입력" 항목은 시스템이 자동으로 붙으므로 options에 넣지 않습니다.
5. 질문은 한 번에 하나만, 존댓말로 작성합니다.
6. 반드시 주어진 JSON 스키마 형식으로만 출력합니다."""

USER_PROMPT = """[신고 위치]
{location_text}

[제보자 부연 설명]
{description}

[사진 판독 결과]
- 위험 요소: {detected_hazard}
- 사진 분석: {photo_analysis}
- 위험성: {risk}
- 노출 대상: {exposed_target}

[지금까지의 질의응답]
{qa_history}

이제 {question_index}번째 질문(총 {question_count}개 중)을 만드세요."""

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "text": {"type": "string", "description": "제보자에게 던질 질문 한 문장"},
        "options": {
            "type": "array",
            "description": "서로 겹치지 않는 선택지 3개",
            "minItems": 3,
            "maxItems": 3,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "value": {"type": "string", "description": "영문 소문자 스네이크케이스 식별자"},
                    "label": {"type": "string", "description": "사용자에게 보일 한국어 선택지 문장"},
                },
                "required": ["value", "label"],
            },
        },
    },
    "required": ["text", "options"],
}


class Option(BaseModel):
    value: str
    label: str


class QuestionRequest(BaseModel):
    session_id: str
    location_text: str
    description: str
    vision: VisionResult
    qa_history: str | None = Field(default=None, description="정리된 질의응답 텍스트")
    qa_pairs: list[QaPair] | None = Field(default=None, description="qa_history 대신 쓸 구조체")
    question_index: int = Field(ge=1, description="이번 질문 번호 (1~3)")
    question_count: int = Field(default=3, ge=1, description="총 질문 수")


class QuestionResponse(BaseModel):
    text: str
    options: list[Option]


def fix_options(text: str, options: list) -> dict:
    """선택지를 정확히 3개로 보정한다 (Dify code 노드 1802000003 그대로).

    minItems/maxItems 를 무시하는 모델이 흔하다.
    """
    fixed, seen = [], set()
    for o in options or []:
        if not isinstance(o, dict):
            continue
        value = str(o.get("value", "")).strip()
        label = str(o.get("label", "")).strip()
        if not value or not label or value in seen:
            continue
        seen.add(value)
        fixed.append({"value": value, "label": label})
    fixed = fixed[:3]
    n = 0
    while len(fixed) < 3:
        value = "unknown" if n == 0 else "unknown_%d" % n
        if value not in seen:
            seen.add(value)
            fixed.append({"value": value, "label": UNKNOWN_LABEL})
        n += 1
    return {"text": (text or "").strip(), "options": fixed}


def run(req: QuestionRequest) -> QuestionResponse:
    result = generate_structured(
        node="1802000002 질문생성기",
        system_prompt=SYSTEM_PROMPT,
        user_prompt=USER_PROMPT.format(
            location_text=req.location_text,
            description=req.description,
            detected_hazard=req.vision.detected_hazard,
            photo_analysis=req.vision.photo_analysis,
            risk=req.vision.risk,
            exposed_target=req.vision.exposed_target,
            qa_history=format_qa_history(req.qa_history, req.qa_pairs, empty=NO_QA),
            question_index=req.question_index,
            question_count=req.question_count,
        ),
        schema=SCHEMA,
        temperature=TEMPERATURE,
    )
    return QuestionResponse(**fix_options(result.get("text", ""), result.get("options", [])))


router = APIRouter(tags=["② 확인 질문 생성"])


@router.post("/v1/questions/next", response_model=QuestionResponse)
def next_question(req: QuestionRequest) -> QuestionResponse:
    """판독 결과와 직전 답변을 보고 확인 질문 1문 + 선택지 3개를 만든다."""
    return run(req)
