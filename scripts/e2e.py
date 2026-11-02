"""로컬 end-to-end 실험 러너.

실행 중인 AI 서버에 ① → ②×3 → ③ → ④(신규) → ④(재생성) → ⑤ 순서로 호출하고
각 단계의 응답을 보여준다. 백엔드가 하게 될 오케스트레이션을 그대로 흉내낸 것이다.

    python scripts/e2e.py --photo ./scripts/sample.jpg
    python scripts/e2e.py --photo https://.../signed.jpg --skip-regenerate
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import sys
import time
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
# ⑤ 의 반복 발생 집계가 실제로 잡히도록 픽스처(cases.json)와 같은 건물·층을 기본값으로 둔다.
DEFAULT_LOCATION = "김대건관 2층 서편 복도 (강의실 K208 앞)"
DEFAULT_BUILDING = "김대건관"
DEFAULT_FLOOR = "2"
DEFAULT_DESCRIPTION = "복도 바닥 타일이 깨져서 위험해 보여요. 지나다닐 때마다 걸려서 넘어질 뻔했어요."


def banner(title: str) -> None:
    print(f"\n{'=' * 72}\n  {title}\n{'=' * 72}")


def show(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def call(client: httpx.Client, path: str, body: dict, label: str) -> dict:
    started = time.perf_counter()
    res = client.post(path, json=body)
    elapsed = time.perf_counter() - started
    if res.status_code != 200:
        print(f"[{label}] 실패 HTTP {res.status_code}\n{res.text}", file=sys.stderr)
        raise SystemExit(1)
    print(f"[{label}] {elapsed:.2f}s")
    return res.json()


def photo_payload(photo: str) -> dict:
    """로컬 파일이면 base64 로, URL 이면 그대로 넘긴다."""
    if photo.startswith(("http://", "https://", "data:")):
        return {"url": photo}
    path = Path(photo)
    if not path.is_file():
        print(f"사진 파일이 없다: {path}", file=sys.stderr)
        raise SystemExit(1)
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    return {"base64": base64.b64encode(path.read_bytes()).decode(), "mime_type": mime}


def main() -> None:
    parser = argparse.ArgumentParser(description="다시고침 AI 서버 end-to-end 실험")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--photo", default=str(ROOT / "scripts" / "sample.jpg"))
    parser.add_argument("--location", default=DEFAULT_LOCATION)
    parser.add_argument("--building", default=DEFAULT_BUILDING, help="⑤ 반복 발생 집계 대상 건물명")
    parser.add_argument("--floor", default=DEFAULT_FLOOR, help="⑤ 동일 층 집계용")
    parser.add_argument("--description", default=DEFAULT_DESCRIPTION)
    parser.add_argument("--question-count", type=int, default=3)
    parser.add_argument("--comment", default="야간에 조명이 어두워 더 위험하다는 점을 넣어 주세요.")
    parser.add_argument("--skip-regenerate", action="store_true")
    parser.add_argument("--out", default=str(ROOT / "scripts" / "out"))
    args = parser.parse_args()

    session_id = f"e2e-{uuid.uuid4().hex[:8]}"
    transcript: dict[str, object] = {"session_id": session_id}

    with httpx.Client(base_url=args.base_url, timeout=180.0) as client:
        health = client.get("/health").json()
        banner(f"health · session={session_id}")
        show(health)
        if not health.get("api_key_loaded"):
            print("GEMINI_API_KEY 가 로드되지 않았다. ai/.env 확인.", file=sys.stderr)
            raise SystemExit(1)

        # ① 사진 판독
        banner("① 사진 판독 · POST /v1/vision/analyze")
        vision = call(
            client,
            "/v1/vision/analyze",
            {
                "session_id": session_id,
                "location_text": args.location,
                "description": args.description,
                "photo": photo_payload(args.photo),
            },
            "vision",
        )
        show(vision)
        transcript["vision"] = vision

        vision_core = {k: vision[k] for k in ("detected_hazard", "photo_analysis", "risk", "exposed_target")}

        # ② 확인 질문 3회 — 백엔드가 question_index 1·2·3 으로 반복 호출하는 부분
        qa_pairs: list[dict] = []
        for index in range(1, args.question_count + 1):
            banner(f"② 확인 질문 {index}/{args.question_count} · POST /v1/questions/next")
            question = call(
                client,
                "/v1/questions/next",
                {
                    "session_id": session_id,
                    "location_text": args.location,
                    "description": args.description,
                    "vision": vision_core,
                    "qa_pairs": qa_pairs,
                    "question_index": index,
                    "question_count": args.question_count,
                },
                f"question#{index}",
            )
            show(question)
            # 실험에서는 첫 번째 선택지를 고른 것으로 가정한다.
            picked = question["options"][0]["label"]
            print(f"  ↳ 선택한 답변: {picked}")
            qa_pairs.append({"question": question["text"], "answer": picked})
        transcript["qa_pairs"] = qa_pairs

        # ③ 담당 부서 판정
        banner("③ 담당 부서 판정 · POST /v1/departments/classify")
        department = call(
            client,
            "/v1/departments/classify",
            {
                "session_id": session_id,
                "location_text": args.location,
                "vision": vision_core,
            },
            "department",
        )
        show(department)
        transcript["department"] = department

        # ④ 신고서 초안 (신규)
        banner("④ 신고서 초안 생성 (신규) · POST /v1/drafts/generate")
        draft = call(
            client,
            "/v1/drafts/generate",
            {
                "session_id": session_id,
                "mode": "generate",
                "location_text": args.location,
                "description": args.description,
                "vision": vision_core,
                "risk_level": vision["risk_level"],
                "qa_pairs": qa_pairs,
                "building_name": args.building,
                "floor": args.floor,
            },
            "draft",
        )
        show(draft)
        transcript["draft"] = draft

        final_draft = draft
        if not args.skip_regenerate:
            banner("④ 신고서 초안 생성 (재생성) · POST /v1/drafts/generate")
            final_draft = call(
                client,
                "/v1/drafts/generate",
                {
                    "session_id": session_id,
                    "mode": "regenerate",
                    "location_text": args.location,
                    "description": args.description,
                    "vision": vision_core,
                    "risk_level": vision["risk_level"],
                    "qa_pairs": qa_pairs,
                    "previous_draft_json": json.dumps(draft, ensure_ascii=False),
                    "comment": args.comment,
                    "building_name": args.building,
                    "floor": args.floor,
                },
                "draft(regenerate)",
            )
            show(final_draft)
            transcript["draft_regenerated"] = final_draft

        # ⑤ 요약·인사이트 (초안 확정 후 비동기 실행 대상)
        banner("⑤ 요약·인사이트 · POST /v1/insights/generate")
        insight = call(
            client,
            "/v1/insights/generate",
            {
                "session_id": session_id,
                "report_id": f"R-{session_id}",
                "location_text": args.location,
                "detected_hazard": final_draft["hazard"],
                "photo_analysis": final_draft["photo_analysis"],
                "risk": final_draft["risk"],
                "risk_level": final_draft["risk_level_final"],
                "draft_json": json.dumps(final_draft, ensure_ascii=False),
                "building_name": args.building,
                "floor": args.floor,
            },
            "insight",
        )
        show(insight)
        transcript["insight"] = insight

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{session_id}.json"
    out_path.write_text(json.dumps(transcript, ensure_ascii=False, indent=2), encoding="utf-8")

    banner("요약")
    print(f"제목       : {final_draft['title']}")
    print(f"위험 등급  : {vision['risk_level']} → {final_draft['risk_level_final']}"
          f" (변경: {final_draft['risk_level_changed']})")
    print(f"담당 부서  : {department['department_name']} ({department['department_code']})"
          f" / assigned_by={department['assigned_by']}")
    print(f"유사 사례  : {final_draft['similar_case_count']}건 {final_draft['similar_case_ids']}")
    print(f"우선순위   : {insight['recommended_priority']} (반복: {insight['is_recurring']})")
    stats = insight.get("recurrence")
    if stats:
        print(f"반복 근거  : 최근 {stats['window_days']}일 {stats['building_name']} {stats['total']}건"
              f" (미해결 {stats['unresolved']}, 동일 층 {stats['same_floor']})")
    print(f"전체 기록  : {out_path}")


if __name__ == "__main__":
    main()
