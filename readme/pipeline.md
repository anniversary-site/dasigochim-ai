# 파이프라인 — 노드별 설명과 프롬프트 버전 관리

다시고침 AI 서버는 원래 Dify 로 만들었던 앱 5개를 **Dify 없이** FastAPI + Gemini 로 옮긴 것입니다.
대응 관계는 아주 단순합니다.

> Dify 앱 1개 = REST 엔드포인트 1개 = 파이썬 모듈 1개 (`app/apps/a1..a5`)

워크플로 엔진은 없습니다. 노드 배선은 각 모듈의 `run()` / `_run()` 안에서 그냥 파이썬 흐름으로 이어집니다.
LLM 노드 하나를 실행하는 것은 `app/llm/gemini.py` 의 `generate_structured()` 를 한 번 부르는 것과 같습니다.

[← README 로 돌아가기](../README.md)

---

## 목차

- [백엔드가 부르는 순서](#백엔드가-부르는-순서)
- [공통 규칙](#공통-규칙)
- [① 사진 판독 — `a1_vision`](#-사진-판독--a1_vision)
- [② 확인 질문 생성 — `a2_question`](#-확인-질문-생성--a2_question)
- [③ 담당 부서 판정 — `a3_department`](#-담당-부서-판정--a3_department)
- [④ 신고서 초안 생성 — `a4_draft`](#-신고서-초안-생성--a4_draft)
- [⑤ 요약·인사이트 — `a5_insight`](#-요약인사이트--a5_insight)
- [위험도 판정 기준 (vision v4 · risk v2)](#위험도-판정-기준-vision-v4--risk-v2)
- [프롬프트 버전 관리](#프롬프트-버전-관리)
- [Dify 와 달라진 점](#dify-와-달라진-점)

---

## 백엔드가 부르는 순서

AI 서버를 부르는 쪽은 **Spring 백엔드뿐**입니다. 프론트는 AI 서버를 직접 호출하지 않습니다.
백엔드의 `HttpAiAnalysisClient` (dasigochim-backend 저장소의
`src/main/java/com/dasigochim/ai/client/HttpAiAnalysisClient.java`) 주석에 적힌 실제 순서는 이렇습니다.

```
질문 단계 : ① /v1/vision/analyze → ② /v1/questions/next   (답변 하나 받을 때마다 1회, 기본 3회)
초안 단계 : (① 결과는 캐시 재사용) → ④ /v1/drafts/generate → ③ /v1/departments/classify
제출 이후 : (① 결과는 캐시 재사용) → ⑤ /v1/insights/generate   (비동기, 담당자용)
```

`scripts/e2e.py` 는 같은 흐름을 로컬에서 흉내 냅니다 (① → ②×3 → ③ → ④ 신규 → ④ 재생성 → ⑤).

## 공통 규칙

| 항목 | 내용 | 근거 |
|---|---|---|
| 무상태 | 서버는 세션을 들고 있지 않습니다. 맥락(`vision`, `qa_history`)은 호출자가 매번 넘깁니다. `session_id` 는 로그·추적 상관관계용입니다 | `app/common.py` |
| 공통 입력 `VisionResult` | ① 의 결과 중 `detected_hazard` · `photo_analysis` · `risk` · `exposed_target` (+ `object_confidence`=1.0, `object_candidates`=[] 기본값). ②③④ 가 받습니다 | `app/common.py` |
| `qa_history` / `qa_pairs` | 정리된 문자열(백엔드 방식) 또는 `[{question, answer}]` 구조체(실험 편의). 둘 다 있으면 문자열이 우선 | `format_qa_history()` |
| `prompt_versions` | 모든 요청 본문에 넣을 수 있는 `{노드: 버전}` 딕셔너리. 그 호출 1회에만 적용 | `PromptSelectable` |
| 구조화 출력 | 프롬프트 YAML 의 `schema` 를 Gemini `response_json_schema` 로 그대로 넘기고 JSON 으로 받습니다 | `app/llm/gemini.py` |
| 재시도 | JSON 파손·빈 응답·429/5xx 류는 `LLM_MAX_RETRIES`(기본 2)회까지 재시도, 대기 1.5s × 시도 차수 | `app/llm/gemini.py` |
| 오류 매핑 | `LlmError` → `502 {"error":"llm_failed"}`, `BackendError` → `502 {"error":"backend_failed", "upstream_status": …}` | `app/main.py` |
| 추적 | API 호출 1회 = Langfuse 트레이스 1개, LLM 호출 1회 = generation 1개. 자세한 내용은 [langfuse.md](langfuse.md) | `app/llm/tracing.py` |

`risk_level` 타입은 코드상 `low | medium | high | critical` 입니다 (`app/common.py` 의 `RiskLevel`).
다만 현재 기본 프롬프트(vision v4, risk v2)는 **`low` / `medium` / `high` 3단계만** 냅니다. `critical` 은 옛 버전과의 호환을 위해 타입에만 남아 있습니다.

---

## ① 사진 판독 — `a1_vision`

| | |
|---|---|
| 엔드포인트 | `POST /v1/vision/analyze` (JSON) · `POST /v1/vision/analyze-upload` (multipart, `/docs` 시험용) |
| 모듈 | `app/apps/a1_vision.py` |
| 프롬프트 노드 | `vision` — 현재 기본 **v4** (`app/prompts/vision/v4.yaml`, temperature 0.2) |
| 배선 | Start → 초기 분석(LLM, 이미지 첨부) → End |
| 외부 호출 | 없음 (Gemini 만) |

**입력** (`VisionAnalysisRequest`)

| 필드 | 설명 |
|---|---|
| `session_id` | 신고 세션 식별자 |
| `location_text` | 신고 위치 |
| `description` | 제보자 부연 설명. 비어 있으면 "(제보자 설명 없음 — 사진 단독으로 판독할 것)" 으로 바꿔 넣습니다 |
| `photo` | `url`(signed URL / 로컬 경로 / `data:` URI) 또는 `base64` 중 하나 필수, `mime_type` 기본 `image/jpeg` |

백엔드는 현재 `photo.base64` + `mime_type` 으로 보냅니다 (백엔드 `AiServerClient.PhotoPayload`).

**출력** (`VisionAnalysisResponse`)

| 필드 | 설명 |
|---|---|
| `detected_hazard` · `photo_analysis` · `risk` · `exposed_target` | 판독 본문. ②③④⑤ 의 입력이 됩니다 |
| `risk_level` | 잠정 위험 등급 |
| `object_confidence` · `object_candidates` | 객체 식별 확신도(0~1)와 후보. v1·v2 프롬프트에는 없어서 1.0 / [] 로 채웁니다 |
| `likelihood` · `severity` | 가능성·중대성 1~3 (v4~) |
| `mandatory_high_category` | "무조건 high" 유형 이름, 해당 없으면 빈 문자열 (v4~) |
| `ongoing_emergency` | 지금 진행 중인 긴급 상황 여부 — 119 안내 대상 (v4~) |
| `prompt_version` | 이번 판독에 쓰인 프롬프트 버전 |

**업로드 판** `analyze-upload` 은 `image/*` 만, 10MB 까지 받습니다 (초과 413, 형식 불일치 415, 빈 파일 400).
`location_text` · `description` · `session_id` · `prompt_version` 은 폼 필드이고 비워도 기본값으로 돕니다.
`prompt_version` 을 채우면 그 버전으로만 1회 판독하므로 `/docs` 에서 바로 A/B 비교를 할 수 있습니다.

**추적 점수**: `object_confidence`(NUMERIC), `risk_level`(CATEGORICAL).

**버전 이력** (`changelog` 요약)

| 버전 | 내용 |
|---|---|
| v1 | Dify `01_vision_analysis.yml` 원문. 사진 단독 판독. 회귀 비교용 |
| v2 | 제보자 부연 설명을 판독의 기준으로 올리고, 사진은 그 진술을 확인·구체화하는 증거로 재정의. 사진 밖 진술도 출처를 밝혀 남김 |
| v3 | 객체 식별 허들 — 손잡이/난간처럼 헷갈리는 쌍을 명시, 확신 없으면 상위 개념으로. `object_confidence` / `object_candidates` 추가 |
| **v4** (기본) | 위험도를 **가능성 × 중대성** 점수표로 판정 (아래 [위험도 판정 기준](#위험도-판정-기준-vision-v4--risk-v2)). `critical` 제거, `ongoing_emergency` 등 4개 필드 추가 |

> **왜 v2 부터 원문과 다르게 갔나?** Dify 원문은 "사진에서 실제로 보이는 상태만" 쓰라고 못 박아서
> 모델이 제보자 설명을 통째로 버렸습니다. 현장을 본 사람은 제보자뿐이고, 사진에 안 담기는 정보
> (밟으면 덜컹거림, 냄새, 언제부터인지)가 핵심인 경우가 많습니다. 그래서 환각 방지를
> "출처를 밝혀 적는다" 로 바꿨습니다 — `photo_analysis` 는 사진으로 확인한 것과 제보자에게 들은 것을
> 문장 안에서 구분해 씁니다. ④⑤ 가 `photo_analysis` 를 그대로 인용하므로 이 구분이 유지되어야 합니다.

---

## ② 확인 질문 생성 — `a2_question`

| | |
|---|---|
| 엔드포인트 | `POST /v1/questions/next` |
| 모듈 | `app/apps/a2_question.py` |
| 프롬프트 노드 | `question` — 기본 **v3** (temperature 0.7) · `object_check` — v1 (temperature 0.5) |
| 배선 | Start → 질문생성기(LLM) → 선택지 3개 보정(Code) → End |
| 외부 호출 | 없음 |

백엔드는 답변이 하나 들어올 때마다 `question_index` 를 1, 2, 3 으로 올려 가며 부릅니다.

**입력** (`QuestionRequest`): `session_id`, `location_text`, `description`, `vision`(`VisionResult`),
`qa_history` 또는 `qa_pairs`, `question_index`(≥1), `question_count`(기본 3), `asked_axes`(이미 쓴 질문 축 목록, 선택).

**출력** (`QuestionResponse`)

| 필드 | 설명 |
|---|---|
| `text` | 질문 1문 |
| `options` | `[{value, label}]` **정확히 3개** |
| `axis` | 이 질문이 다룬 축. 다음 호출의 `asked_axes` 에 넣으면 됩니다 |
| `prompt_version` | 사용된 프롬프트 버전 |

**두 갈래 분기 — LLM 이 아니라 코드가 정합니다**

- `question_index == 1` 이고 `vision.object_confidence < OBJECT_CONFIRM_THRESHOLD`(기본 0.7) 이면
  → `object_check` 노드로 "판독기가 본 것이 맞는지" 확인하는 질문을 냅니다 (`axis = "object_check"`).
  객체를 잘못 특정하면 ③ 부서 배정과 ④ 신고서가 통째로 어긋나므로, 되묻는 비용이 가장 쌉니다.
- 그 외에는 `question` 노드로 아직 묻지 않은 축의 질문을 만듭니다.

두 노드의 응답 형태가 같아서 백엔드·프론트는 분기를 몰라도 됩니다.
단, 백엔드가 ① 응답의 `object_confidence` / `object_candidates` 를 ② 요청의 `vision` 에 넘겨줘야 분기가 실제로 켜집니다 (백엔드 `VisionPayload` 가 넘기고 있습니다).

**코드 후처리**

- `fix_options()` — 선택지를 정확히 3개로 맞춥니다. 중복·빈 값은 버리고, 모자라면 `unknown` / "잘 모르겠어요" 로 채웁니다 (Dify code 노드 1802000003 포팅).
- 축 중복 재시도 — 모델이 고른 `axis` 가 `asked_axes` 에 이미 있으면, 그 축을 금지 목록에 더해 **한 번만** 다시 생성합니다.

**질문 축** (`question/v3.yaml` 의 `axes`): `sensory` · `onset` · `recurrence` · `traffic` · `temporary_action` · `scale` · `cause` · `impact` · `access`

**버전 이력**

| 버전 | 내용 |
|---|---|
| v1 | Dify 원문. 예시 4개가 앵커가 되어 매번 같은 질문이 나왔음 |
| v2 | 예시를 지우고 9개 질문 축을 정의. 이미 쓴 축을 빼고 고르며 `axis` 를 출력해 코드가 중복을 검증 |
| **v3** (기본) | 청년안심주택 생활 민원 맞춤. 모든 불편을 안전 위험으로 해석하지 않고, "공유주방을 늘려 주세요" 같은 해결책 제안은 **그 이유부터** 확인. 우선순위 `impact → cause → access → traffic → scale → onset → recurrence → sensory → temporary_action`. "직접 입력" 은 화면이 따로 제공하므로 선택지에 넣지 않음 |
| object_check v1 | 신규 노드. 객체 오인(손잡이 ↔ 난간 등)을 제보자에게 되물음 |

---

## ③ 담당 부서 판정 — `a3_department`

| | |
|---|---|
| 엔드포인트 | `POST /v1/departments/classify` |
| 모듈 | `app/apps/a3_department.py` |
| 프롬프트 노드 | `department` — v1 (temperature 0.2) |
| 배선 | 검색 요청 조립 → **C-1** 업무범위 검색 → 후보 목록 조립 → 담당 부서 매칭(LLM) → **C-2** 확인/fallback → 결과 파싱 |
| 외부 호출 | 백엔드 C-1, C-2 ([configuration.md](configuration.md#백엔드-내부-계약-c-1--c-2--c-5)) |

**입력** (`DepartmentRequest`): `session_id`, `location_text`, `vision`, `top_k`(비우면 8).

**출력** (`DepartmentResponse`)

| 필드 | 설명 |
|---|---|
| `department_code` · `department_name` · `department_contact` | 확정된 부서 |
| `assigned_by` | `agent`(모델 판정 채택) 또는 `fallback`(기본 부서로 배정) |
| `confidence` | 확정 신뢰도. fallback 이면 **-1** |
| `rationale` | 판정 근거 |
| `llm_confidence` | 모델이 낸 원래 신뢰도 |
| `candidate_count` · `corpus_version` | C-1 후보 수와 업무범위 코퍼스 버전 |

- C-1 이 실패하면 ③ 전체가 실패합니다 (부서 판정의 필수 입력이라 의도된 동작 → 502).
- 코드 유효성 검증과 임계값 fallback 은 결정론이라 **백엔드(C-2) 몫**입니다. stub 모드에서는 `DEPARTMENT_CONFIDENCE_THRESHOLD`(0.7) / `DEFAULT_DEPARTMENT_CODE`(`SAFETY_CENTER`) 로 흉내 냅니다.
- `confidence: -1` 은 Dify number 출력이 null 을 못 담아서 생긴 규칙인데, 백엔드가 이미 기대할 수 있어 유지했습니다.

**추적 점수**: `department_fallback`(BOOLEAN — 이 노드의 실패율), `llm_confidence`(NUMERIC).

---

## ④ 신고서 초안 생성 — `a4_draft`

| | |
|---|---|
| 엔드포인트 | `POST /v1/drafts/generate` |
| 모듈 | `app/apps/a4_draft.py` (+ `app/cases.py`, `app/rag/`) |
| 프롬프트 노드 | `risk` **v2** (temperature 0.0) · `draft` **v3** (0.3) · `qa_reflection` v1 (0.0) |
| 배선 | 사례검색 조립 → **C-5** 과거사례(실패 허용) → 위험도 재산출(LLM) → 문체 견본 검색(RAG, 실패 허용) → mode 분기 → 문서생성기(LLM) → 질의응답 반영 검증(LLM) → 필요 시 보정(LLM) → 재검증 |
| 외부 호출 | 백엔드 C-5, Gemini 임베딩(문체 RAG) |

**입력** (`DraftRequest`)

| 필드 | 설명 |
|---|---|
| `session_id`, `location_text`, `description`, `vision` | 공통 |
| `mode` | `generate`(기본) 또는 `regenerate` |
| `risk_level` | 판독 시점의 잠정 위험 등급 |
| `qa_history` / `qa_pairs` | 확인 질문 답변 |
| `previous_draft_json`, `comment` | `regenerate` 전용 — 이전 초안과 수정 요청 |
| `top_k` | 과거사례 개수 (비우면 4) |
| `building_id`, `building_name`, `floor` | 과거사례 관련성 기준 (선택) |

**출력** (`DraftResponse`)

| 필드 | 설명 |
|---|---|
| `title`, `hazard_place`, `occurrence_location`, `hazard`, `photo_analysis`, `risk`, `exposed_target`, `action_urgent`, `action_fundamental` | 신고서 9개 항목 (draft v2 에서 `other_opinion` 제거) |
| `risk_level_final` · `risk_level_rationale` · `risk_level_changed` | `risk` 노드가 질의응답까지 반영해 재산출한 등급 |
| `similar_case_ids` · `similar_case_count` | 참고한 과거사례 |
| `style_reference_ids` · `style_reference_source` | 붙은 문체 견본과 선택 방식 (`embedding` / `hash` / `role-order` / `none`) |
| `qa_reflection_passed` · `qa_reflection_score` · `qa_reflection_missing_facts` · `qa_reflection_corrected` | 질의응답 반영 검증 결과 |

**흐름의 포인트**

1. **위험도 재산출** — `risk` 노드가 질의응답에서 확인된 사실로 등급을 다시 매깁니다 (아래 [위험도 판정 기준](#위험도-판정-기준-vision-v4--risk-v2)).
2. **참고 자료 두 종류는 절대 섞지 않습니다.**
   - `[유사 과거사례]` — C-5 · 같은 주택의 실제 민원 기록 → 내용을 참고해도 됩니다
   - `[문체 참조]` — `app/rag` · 고용노동부 재해조사의견서 발췌 → **문장 골격만**. 내용 차용은 오답 (draft v3 규칙 14·15)
3. **문체 검색은 `{style_reference}` 자리표시자가 있는 버전에서만** 돕니다. v1·v2 로 되돌리면 임베딩 호출도 하지 않습니다. 자세한 내용은 [style-rag.md](style-rag.md).
4. **mode 분기** — `regenerate` 면 draft 프롬프트 파일 안의 `regenerate_system_suffix` / `regenerate_user_suffix` 를 붙입니다. 본문과 접미사가 한 파일에 있어야 버전을 되돌릴 때 짝이 안 어긋납니다.
5. **질의응답 반영 검증** — 답변이 있으면 `qa_reflection` 노드가 핵심 사실이 초안에 반영됐는지 의미 단위로 검사합니다. 빠졌으면 누락 사실만 명시해 **한 번** 보정 생성하고 다시 검증합니다. (`tests/test_draft_qa_reflection.py` 가 이 경로를 다룹니다.)
6. **과거사례 실패 허용** — C-5 가 실패해도 `(과거사례 조회 실패 — 참고 없이 작성)` 으로 우회합니다 (`app/cases.py`).

**추적 점수**: `qa_reflection_coverage`(NUMERIC), `qa_reflection_passed`(BOOLEAN).

**버전 이력**

| 노드·버전 | 내용 |
|---|---|
| draft v1 | Dify `04_draft.yml` 원문 |
| draft v2 | "기타 의견"(`other_opinion`) 제거 — 채울 것이 없는 칸을 모델이 억지로 채웠음 |
| **draft v3** (기본) | 재해조사의견서 문체 RAG 도입. 확인/추정을 어미로 가르고(`~임` / `~것으로 추정됨`), 조치를 `[대상]+[방법]+[기준]` 으로 닫음. 두 참고 블록의 범위를 명시 |
| risk v1 | Dify 원문 (위험도 재산출) |
| **risk v2** (기본) | vision v4 와 같은 가능성 × 중대성 점수표로 재판정. 무조건 high 유형은 내리지 않음. `critical` 없이 3단계 |
| qa_reflection v1 | 자유 입력을 포함한 질의응답 핵심 사실 반영 여부 검사 |

---

## ⑤ 요약·인사이트 — `a5_insight`

| | |
|---|---|
| 엔드포인트 | `POST /v1/insights/generate` |
| 모듈 | `app/apps/a5_insight.py` (+ `app/cases.py`) |
| 프롬프트 노드 | `summary` v1 (temperature 0.4) · `insight` v1 (0.5) |
| 배선 | 사례검색 조립 → **C-5**(실패 허용) → 목록·집계 조립 → 제목·요약(LLM) → 인사이트(LLM) |
| 외부 호출 | 백엔드 C-5 |

초안 확정 후 백엔드가 **비동기로** 부르는 것을 전제합니다 (사용자 대면 경로에 지연을 주지 않기 위해). AI 서버 자체는 동기 HTTP 입니다.

**입력** (`InsightRequest`): `session_id`, `report_id`(집계·목록에서 이번 건 제외), `location_text`, `detected_hazard`, `photo_analysis`, `risk`, `risk_level`(확정 등급), `draft_json`(확정 초안 JSON 문자열), `top_k`, `building_id` · `building_name` · `floor`(반복 집계 대상).

**출력** (`InsightResponse`)

| 필드 | 설명 |
|---|---|
| `summary` · `keywords` | 담당 부서용 요약과 키워드 |
| `is_recurring` | 반복 발생 여부 — **LLM 판단이 아니라 집계 수치로** 결정 |
| `insight` · `recommended_priority` | 인사이트 문장, `immediate` / `high` / `normal` / `low` |
| `related_report_ids` · `similar_case_ids` · `similar_case_count` | 관련 신고 |
| `recurrence` | `is_recurring` 의 근거가 된 집계 원본 (`CaseStats`) |

**반복 발생 판정** (`CaseBundle.is_recurring`): C-5 의 `stats.total`(이번 건 제외 과거 건수) + 1 ≥ `RECURRING_THRESHOLD`(기본 3) 이면 반복입니다.
집계 기간은 `CASE_WINDOW_DAYS`(기본 30일). 집계가 없으면(위치 미상·조회 실패) `false` 입니다.
집계 텍스트에는 모든 수치에 적용 범위(`[건물 전체]` / `[건물 2층]`)를 붙입니다 — 범위를 안 적으면 모델이 "2층에서 3건" 처럼 수치를 뒤섞었기 때문입니다.

> 현재 백엔드의 `InsightRequest` 는 `building_id` / `building_name` / `floor` 를 보내지 않습니다
> (백엔드 `AiServerClient.InsightRequest`). 이 경우 집계를 건너뛰므로 `is_recurring` 은 `false`, `recurrence` 는 `null` 이 됩니다.

---

## 위험도 판정 기준 (vision v4 · risk v2)

국가 단일 기준이 없어서, 고용노동부 「사업장 위험성평가에 관한 지침」(2023 개정)의 **가능성(빈도) × 중대성(강도)** 방식과
**KOSHA GUIDE H-205-2018** 의 3×3 곱셈 매트릭스를 준용합니다. `vision/v4.yaml` 과 `risk/v2.yaml` 이 같은 표를 씁니다.

| likelihood (가능성) | 기준 |
|---|---|
| 3 | 지금 진행 중이거나, 입주민이 매일 지나다니며 노출됨 (매일 쓰는 복도·현관·주차장 통로) |
| 2 | 특정 조건이 맞으면 발생 (비 올 때, 밤에, 특정 시설을 쓸 때) |
| 1 | 거의 노출되지 않음 (출입 통제 구역, 드물게 쓰는 공간) |

| severity (중대성) | 기준 |
|---|---|
| 3 | 사망·중상 가능 (추락, 감전, 화재, 가스 중독, 끼임, 무거운 낙하물) |
| 2 | 경상 가능 (넘어짐, 베임, 부딪힘, 경미한 화상) |
| 1 | 부상 없음, 불편·재산 피해 (고장, 소음, 악취, 오염, 미관) |

| 점수 (L × S) | 등급 | 의미 |
|---|---|---|
| 6 ~ 9 | `high` | 허용 불가 — 즉시 개선 |
| 3 ~ 4 | `medium` | 개선 필요 |
| 1 ~ 2 | `low` | 허용 가능 — 계획 보수 |

**무조건 high** — 점수와 상관없이 high 로 올리고 `mandatory_high_category` 에 유형을 적습니다:
가스 · 전기 · 화재 설비 · 추락 · 승강기 · 구조 · 출입 보안(1인 청년 가구 안전).

**진행 중인 긴급 상황** — 불길·연기, 진행 중인 가스 누출, 사람이 다쳤거나 갇힌 상황은 앱 처리 대상이 아니라 119 · 가스 공급사 안내가 먼저입니다.
등급 대신 `ongoing_emergency: true` 로 표시하고 등급은 `high` 로 둡니다. 그래서 `critical` 이 없어졌습니다.

risk v2 는 잠정 등급이 `critical` 이었다면 `high` 로 보고, `rationale` 에 "가능성 n × 중대성 m = 점수 → 등급" 형식으로 근거를 적습니다.

---

## 프롬프트 버전 관리

프롬프트는 파이썬 코드가 아니라 `app/prompts/` 에 있습니다.
`app/apps/*.py` 에는 배선과 요청/응답 모델만 있습니다.

```
app/prompts/
  __init__.py          로더 · 버전 결정 · 카탈로그
  langfuse_source.py   Langfuse 연동 (실패 시 파일로 폴백)
  defaults.yaml        노드별 활성 버전
  <노드>/vN.yaml       name · version · node · changelog · temperature · system · user · schema (+ 노드별 부가 키)
```

필수 키는 `node`, `system`, `user`, `schema`, `temperature` 입니다. 하나라도 없으면 로드 시점에 `PromptError` 가 납니다.
user 템플릿은 `str.format()` 의 `{변수}` 표기를 씁니다.

### 현재 활성 버전 (`app/prompts/defaults.yaml`)

| 노드 | 쓰는 곳 | 활성 | 있는 버전 |
|---|---|---|---|
| `vision` | ① 사진 판독 | **v4** | v1 · v2 · v3 · v4 |
| `question` | ② 확인 질문 | **v3** | v1 · v2 · v3 |
| `object_check` | ② 객체 확인 질문 | v1 | v1 |
| `department` | ③ 부서 매칭 | v1 | v1 |
| `risk` | ④ 위험도 재산출 | **v2** | v1 · v2 |
| `draft` | ④ 신고서 초안 | **v3** | v1 · v2 · v3 |
| `qa_reflection` | ④ 질의응답 반영 검증 | v1 | v1 |
| `summary` | ⑤ 제목·요약 | v1 | v1 |
| `insight` | ⑤ 인사이트 | v1 | v1 |

### 버전 고르기 — 좁은 것이 이깁니다

1. **요청 본문** `prompt_versions` — 그 호출 1회에만 적용, 재시작 불필요
   ```jsonc
   {"session_id": "...", "prompt_versions": {"vision": "v2"}}
   ```
2. **환경변수** `PROMPT_VERSIONS="vision=v2,question=v1"` — 이 서버 전체
3. **`app/prompts/defaults.yaml`** 의 `active` — 기본값

세 경로 모두 같은 식별자(`v1`, `v2`, …)를 씁니다. 파일 모드에서는 파일 이름이고, Langfuse 모드에서는 label 입니다.

### 관리 API

| 엔드포인트 | 용도 |
|---|---|
| `GET /v1/prompts` | 노드별 활성 버전 · 고를 수 있는 버전 · changelog |
| `GET /v1/prompts/{노드}/{버전}` | 그 버전의 system · user · schema 전문 (없으면 404) |
| `POST /v1/prompts/reload` | YAML 을 고친 뒤 캐시만 비움 (재시작 없이 반영) |

활성 버전이 하나라도 안 읽히면 **기동 시점에 실패합니다** (`app/main.py` 의 `check_prompts` → `prompts.verify_all()`).
첫 호출 때 502 로 나타나면 원인을 찾기 어렵기 때문입니다.

### 새 버전 만들기

1. `app/prompts/<노드>/vN.yaml` 을 복사해 `vN+1.yaml` 로 만들고 `version` · `changelog` 를 고칩니다.
2. `prompt_versions` 로 한 번씩 비교해 보고 (`/docs` 의 `analyze-upload` 는 `prompt_version` 폼 필드가 있습니다),
3. 괜찮으면 `defaults.yaml` 의 `active` 를 바꿉니다.
4. Langfuse 를 쓴다면 `scripts/sync_prompts.py` 로 올립니다 → [langfuse.md](langfuse.md).

### 프롬프트 소스 (`PROMPT_SOURCE`)

- `file` (기본) — 위 YAML 을 읽습니다. **git 이 진실입니다.**
- `langfuse` — Langfuse 프롬프트 관리에서 읽고, 어떤 실패든 파일로 자동 폴백합니다 (경고 로그 1줄).

자세한 동기화·추적 방식은 [langfuse.md](langfuse.md) 를 보세요.

---

## Dify 와 달라진 점

원본 Dify 앱의 노드 구성과 원문 프롬프트는 [`../노드별설명.md`](../노드별설명.md) 에 남아 있습니다 (자동 생성 문서).

- **워크플로 엔진 없음.** ④ 의 `mode 분기`(IF/ELSE 노드)는 `if req.mode == "regenerate"` 한 줄이 됐습니다.
- **Dify code 노드는 로직 그대로 포팅** — 선택지 3개 보정, 후보 목록 조립, 과거사례 조립, 검색 질의 조립(`join_query`).
- **사진 입력이 넓어졌습니다.** `photo.url`(signed URL / 로컬 경로 / `data:` URI) 또는 `photo.base64`.
- **`qa_history` 를 구조체로도 받습니다** (`qa_pairs`). 문자열이 있으면 그쪽이 우선.
- **`confidence: -1` 규칙 유지** (③ fallback).
- **`thinking_level=low`** — gemini-3 계열 기본 추론을 낮춰 지연을 줄였습니다. 품질이 아쉬우면 `THINKING_LEVEL=high`.
- **프롬프트가 원문에서 진화했습니다.** v1 은 Dify 원문 그대로 보존하고, 이후 버전에서 제보자 설명 우선(vision v2), 질문 축(question v2), 문체 RAG(draft v3), 위험성 매트릭스(vision v4 · risk v2) 등을 더했습니다.
- **새 노드** — `object_check`(②), `qa_reflection`(④) 은 Dify 에 없던 노드입니다.
