# `.claude/skills/`

에이전트가 이 저장소에서 작업할 때 쓰는 skill 을 담아 둔다.

## `langfuse/`

| 항목 | 값 |
|---|---|
| 출처 | <https://github.com/langfuse/skills> — `skills/langfuse/` |
| 받은 커밋 | `4a77f3a` (2026-08-28) |
| 라이선스 | MIT (Copyright (c) 2026 Langfuse GmbH) |
| 설치일 | 2026-08-31 |

**왜 저장소에 넣었나.** 이 서버의 프롬프트 관리와 추적이 전부 Langfuse 위에 있어서,
관련 작업을 할 때마다 같은 기준을 봐야 한다. 각자 로컬에 받게 하면 버전이 갈린다.
MIT 이고 112K 라 넣는 비용이 거의 없다.

**갱신 방법.** Langfuse SDK 를 올릴 때 함께 본다.

```powershell
git clone --depth 1 https://github.com/langfuse/skills.git $env:TEMP\lf-skills
Remove-Item -Recurse -Force .claude\skills\langfuse
Copy-Item -Recurse $env:TEMP\lf-skills\skills\langfuse .claude\skills\langfuse
```

받은 커밋을 위 표에 갱신할 것. **직접 고치지 않는다** — 우리 규칙은 `README.md` 와
`specification/` 에 쓰고, 이 디렉토리는 상류 사본으로만 둔다.

## skill 이 요구하는 것 중 지킨 것

`references/instrumentation.md` 의 baseline 체크리스트 기준이다.

| 요구 | 현재 |
|---|---|
| 모델명 | `generation.model` 에 있음 |
| 토큰 사용량 | `usage_details` (input·output·total·thoughts) |
| 좋은 트레이스 이름 | `① 사진 판독` 처럼 노드 이름 그대로 |
| 스팬 계층 | API 호출 1개 = 트레이스 1개, 그 아래 generation N개 |
| 관찰 타입 | LLM 호출은 `as_type="generation"` |
| 민감정보 마스킹 | `app/llm/masking.py` (`LANGFUSE_MASK_PII`) |
| 트레이스 입출력 | system·user 프롬프트만. **사진 바이트는 싣지 않는다** |
| `session_id` | 백엔드가 주는 `report-{id}` — 신고 1건이 한 세션 |
