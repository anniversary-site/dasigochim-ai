# Langfuse — 프롬프트 소스와 추적

[← README 로 돌아가기](../README.md) · 관련: [pipeline.md — 프롬프트 버전 관리](pipeline.md#프롬프트-버전-관리) · [configuration.md — Langfuse](configuration.md#langfuse)

Langfuse 는 이 서버에서 **두 가지 일**을 합니다. 둘은 서로 독립입니다.

| 기능 | 켜는 조건 | 모듈 |
|---|---|---|
| 프롬프트 소스 | `PROMPT_SOURCE=langfuse` + 키 2개 | `app/prompts/langfuse_source.py` |
| 추적 (tracing) | 키 2개 + `LANGFUSE_TRACING=true`(기본) | `app/llm/tracing.py`, `app/llm/masking.py` |

두 기능 모두 **실패해도 판독을 막지 않습니다.** 시연 중 외부 서비스 때문에 서비스가 멈추면 안 되기 때문입니다.

---

## 1. 프롬프트 소스 (`PROMPT_SOURCE=langfuse`)

**git 의 YAML 이 진실이고 Langfuse 는 그 사본입니다.** 동기화는 한 방향(파일 → Langfuse)뿐입니다.

```bash
python scripts/sync_prompts.py --dry-run              # 무엇이 올라갈지
python scripts/sync_prompts.py                        # 전부 올리기
python scripts/sync_prompts.py --node vision --version v4   # 일부만
```

- 노드 하나 = Langfuse 프롬프트 하나. 이름은 `LANGFUSE_PROMPT_PREFIX` 를 붙여 `dasigochim/vision` 처럼 됩니다.
- system / user 는 chat 프롬프트의 두 메시지로 올라갑니다.
- 파일 버전 `vN` 이 Langfuse **label** 로 올라갑니다. 그래서 요청 본문 · `PROMPT_VERSIONS` · `defaults.yaml` 세 경로가 파일 모드와 Langfuse 모드에서 **같은 식별자**를 씁니다.
- `defaults.yaml` 의 활성 버전에는 `production` 라벨도 함께 붙습니다.
- `temperature` 와 JSON 스키마는 `config` 에 실어 버전과 함께 고정합니다. config 에 둘이 없으면 그 버전은 쓰지 않고 파일로 폴백합니다.
- Langfuse 쪽 캐시 TTL 은 `PROMPT_CACHE_TTL_SECONDS`(기본 60초)입니다.

**폴백** — Langfuse 가 죽거나, 키가 비었거나, 프롬프트가 없거나, 어떤 예외든 나면 **파일 소스로 자동 폴백**하고 경고 로그를 1줄 남깁니다.

**변수 치환** — Langfuse 의 `compile()`(`{{var}}`) 대신 파일 모드와 같은 `str.format()`(`{var}`) 을 씁니다.
두 소스가 같은 렌더링을 타야 "Langfuse 에서만 다르게 나온다" 는 버그가 안 생깁니다. 대신 Langfuse UI 의 변수 자동 인식은 못 씁니다 — 의도한 맞바꿈입니다.

---

## 2. 추적 (tracing)

`LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` 만 채우면 켜집니다. **`PROMPT_SOURCE` 와는 무관합니다** — 프롬프트를 파일에서 읽어도 추적은 돕니다.

```
API 호출 1회 = 트레이스 1개 (trace_name = "⑤ 요약·인사이트")
  └─ generation "1805000005 제목·요약 [summary/v1]"
  └─ generation "1805000006 인사이트 [insight/v1]"
```

- generation 에 프롬프트 이름·버전·소스, 토큰 사용량, temperature(·thinking_level), 시도 횟수가 붙습니다. 재시도 전체가 generation 1개입니다.
- `session_id` 가 트레이스 속성으로 올라가서 Langfuse **Sessions** 화면에서 신고 한 건이 ① → ② × 3 → ③ → ④ → ⑤ 로 묶여 보입니다.
- 어느 프롬프트 버전이 어떤 출력을 냈는지가 자동으로 쌓이므로 `prompt_versions` A/B 의 근거가 됩니다.
- Langfuse 프롬프트 소스를 쓸 때는 generation 이 해당 프롬프트 버전에 링크됩니다.

**노드가 남기는 점수**

| 노드 | 점수 |
|---|---|
| ① | `object_confidence` (NUMERIC), `risk_level` (CATEGORICAL) |
| ③ | `department_fallback` (BOOLEAN), `llm_confidence` (NUMERIC) |
| ④ | `qa_reflection_coverage` (NUMERIC), `qa_reflection_passed` (BOOLEAN) |

**안전 장치**

- **추적 실패는 판독을 막지 않습니다.** 키가 없거나 Langfuse 가 죽으면 전부 no-op 으로 떨어지고 경고는 한 번만 찍습니다.
- **사진 바이트는 싣지 않습니다** (`has_image` 플래그만).
- **개인정보 마스킹** (`LANGFUSE_MASK_PII=true` 기본) — 이메일 · 휴대폰번호 · 유선번호 · 주민등록번호 형태 · 카드번호 형태를 `[제거됨]` 으로 지웁니다 (`app/llm/masking.py`). 위치와 위험 요소 서술은 남깁니다.
  정규식이라 **사람 이름은 걸러지지 않습니다.** 신고 본문에 이름을 적지 않도록 안내하는 것이 더 확실한 방어입니다.
- **환경 분리** — `LANGFUSE_TRACING_ENVIRONMENT`(기본 `local`, 운영 compose 는 `prod`)로 로컬·시연·운영 트레이스를 한 프로젝트 안에서 갈라 봅니다.
- **종료 시 flush** — Langfuse 는 이벤트를 모아 보내므로 shutdown 훅에서 밀어 넣습니다.

**켜졌는지 확인하기**

기동 시 `check_langfuse_auth` 가 실제 인증을 한 번 시도해 로그에 남깁니다. `GET /health` 에서도 볼 수 있습니다.

| `/health` 필드 | 의미 |
|---|---|
| `langfuse_ready` | 키 두 개가 채워졌는지 |
| `langfuse_tracing` | 추적이 켜졌는지 |
| `langfuse_auth` | **실제로 인증되는지.** public/secret 을 뒤바꿔 넣으면 ready 는 true 인데 auth 는 false 입니다 |
| `langfuse_environment` · `langfuse_mask_pii` | 현재 환경 이름과 마스킹 여부 |

SDK 메모: `app/llm/tracing.py` 는 Langfuse Python SDK **4.15.1 설치본 기준**으로 작성되었습니다 (`start_as_current_observation(as_type="generation")`, `propagate_attributes()`).
`requirements.txt` 의 제약은 `langfuse>=3.0` 입니다.

---

## Claude Code skill

`.claude/skills/langfuse/` 에 Langfuse 공식 skill 사본(MIT)이 들어 있습니다. 출처·갱신 방법은 [`../.claude/README.md`](../.claude/README.md) 를 보세요.
