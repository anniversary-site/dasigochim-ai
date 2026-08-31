"""② 확인 질문 생성 — 02_question_gen.yml 대응.

Start → 질문생성기(LLM) → 선택지 3개 보정(Code) → End. 무상태.
백엔드가 question_index 1·2·3 으로 3회 호출한다.

노드가 두 개다. 어느 쪽을 탈지는 **코드가 결정론으로 정한다** (LLM 이 아니다).

- ``question``     평소 질문. 질문 축을 하나 골라 아직 안 물어본 것을 묻는다.
- ``object_check`` ① 이 객체를 확신하지 못했을 때(``object_confidence`` 가 임계값 미만)
                   첫 질문을 "이게 무엇인지" 확인하는 질문으로 바꾼다.

객체를 잘못 특정하면 ③ 부서 배정과 ④ 신고서가 통째로 어긋난다. 되묻는 비용이 가장 싸다.
두 노드의 응답 형태가 같아서 백엔드·프론트는 분기를 몰라도 된다.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app import prompts
from app.common import NO_QA, PromptSelectable, QaPair, VisionResult, format_qa_history
from app.config import get_settings

from app.llm.gemini import generate_structured
from app.llm import tracing

log = logging.getLogger(__name__)

NODE = "question"
OBJECT_CHECK_NODE = "object_check"
OBJECT_CHECK_AXIS = "object_check"

UNKNOWN_LABEL = "잘 모르겠어요"
NO_AXES = "(없음 — 첫 질문입니다)"
NO_CANDIDATES = "(후보 없음 — 판독기가 대안을 제시하지 못했습니다)"


class Option(BaseModel):
    value: str
    label: str


class QuestionRequest(PromptSelectable):
    session_id: str
    location_text: str
    description: str
    vision: VisionResult
    qa_history: str | None = Field(default=None, description="정리된 질의응답 텍스트")
    qa_pairs: list[QaPair] | None = Field(default=None, description="qa_history 대신 쓸 구조체")
    question_index: int = Field(ge=1, description="이번 질문 번호 (1~3)")
    question_count: int = Field(default=3, ge=1, description="총 질문 수")
    asked_axes: list[str] | None = Field(
        default=None,
        description="이미 사용한 질문 축. 넘기면 중복 축을 코드가 걸러낸다 "
                    "(안 넘겨도 qa_history 로 모델이 추론한다)",
    )


class QuestionResponse(BaseModel):
    text: str
    options: list[Option]
    axis: str = Field(default="", description="이 질문이 다룬 축. 다음 호출의 asked_axes 에 넣으면 된다")
    prompt_version: str = Field(default="", description="이번 질문에 쓰인 프롬프트 버전")


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


def needs_object_check(req: QuestionRequest) -> bool:
    """객체 확인 질문으로 갈지 판단한다. 임계값 비교이지 LLM 판단이 아니다.

    첫 질문에서만 묻는다 — 뒤의 질문들이 이 답 위에 쌓이기 때문이고,
    다 물어본 뒤에 "그런데 그게 뭐였죠" 라고 되묻는 것은 늦다.
    """
    if req.question_index != 1:
        return False
    threshold = get_settings().object_confirm_threshold
    return req.vision.object_confidence < threshold


def _object_check(req: QuestionRequest) -> QuestionResponse:
    prompt = prompts.pick(OBJECT_CHECK_NODE, req.prompt_versions)
    candidates = req.vision.object_candidates or []
    result = generate_structured(
        node=f"{prompt.node} [{prompt.label}]",
        system_prompt=prompt.system,
        user_prompt=prompt.render(
            location_text=req.location_text,
            description=req.description,
            object_confidence=f"{req.vision.object_confidence:.2f}",
            detected_hazard=req.vision.detected_hazard,
            photo_analysis=req.vision.photo_analysis,
            object_candidates="\n".join(f"- {c}" for c in candidates) or NO_CANDIDATES,
        ),
        schema=prompt.schema,
        temperature=prompt.temperature,
        prompt=prompt,
    )
    log.info(
        "object_check 발동 session=%s confidence=%.2f candidates=%d",
        req.session_id, req.vision.object_confidence, len(candidates),
    )
    fixed = fix_options(result.get("text", ""), result.get("options", []))
    return QuestionResponse(**fixed, axis=OBJECT_CHECK_AXIS, prompt_version=prompt.version)


def _generate(prompt, req: QuestionRequest, used: list[str]) -> dict:
    return generate_structured(
        node=f"{prompt.node} [{prompt.label}]",
        system_prompt=prompt.system,
        user_prompt=prompt.render(
            location_text=req.location_text,
            description=req.description,
            detected_hazard=req.vision.detected_hazard,
            photo_analysis=req.vision.photo_analysis,
            risk=req.vision.risk,
            exposed_target=req.vision.exposed_target,
            qa_history=format_qa_history(req.qa_history, req.qa_pairs, empty=NO_QA),
            question_index=req.question_index,
            question_count=req.question_count,
            used_axes="\n".join(f"- {a}" for a in used) or NO_AXES,
        ),
        schema=prompt.schema,
        temperature=prompt.temperature,
        prompt=prompt,
    )


def _next_question(req: QuestionRequest) -> QuestionResponse:
    prompt = prompts.pick(NODE, req.prompt_versions)
    used = list(req.asked_axes or [])

    result = _generate(prompt, req, used)
    axis = str(result.get("axis") or "").strip()

    # 축이 겹치면 한 번만 다시 시킨다. 프롬프트로 막는 것과 별개로,
    # 겹친 축을 명시적으로 금지 목록에 넣고 재시도하는 편이 확실하다.
    # v1 프롬프트에는 axis 자체가 없으므로 이 경로를 타지 않는다.
    if axis and axis in used:
        log.info("질문 축 중복 → 재시도 session=%s axis=%s", req.session_id, axis)
        result = _generate(prompt, req, used + [axis])
        axis = str(result.get("axis") or "").strip()

    fixed = fix_options(result.get("text", ""), result.get("options", []))
    return QuestionResponse(**fixed, axis=axis, prompt_version=prompt.version)


def run(req: QuestionRequest) -> QuestionResponse:
    """API 호출 1회 = Langfuse 트레이스 1개. session_id 로 신고 한 건이 묶인다."""
    with tracing.request_trace(req.session_id, "② 확인 질문 생성"):
        return _run(req)


def _run(req: QuestionRequest) -> QuestionResponse:
    if needs_object_check(req):
        return _object_check(req)
    return _next_question(req)


router = APIRouter(tags=["② 확인 질문 생성"])


@router.post("/v1/questions/next", response_model=QuestionResponse)
def next_question(req: QuestionRequest) -> QuestionResponse:
    """판독 결과와 직전 답변을 보고 확인 질문 1문 + 선택지 3개를 만든다.

    `vision.object_confidence` 가 `OBJECT_CONFIRM_THRESHOLD` 미만이고 첫 질문이면,
    질문 대신 **"판독기가 본 것이 맞는지" 확인하는 질문**을 낸다 (응답 형태는 동일).

    응답의 `axis` 를 다음 호출의 `asked_axes` 에 넣어 주면 같은 축을 다시 묻지 않는다.
    """
    return run(req)
