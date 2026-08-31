"""로컬 프롬프트 YAML → Langfuse 업로드.

**git 의 YAML 이 진실이고, Langfuse 는 그 사본이다.** 방향은 한쪽뿐이다.
Langfuse UI 에서 고친 것은 여기로 되돌아오지 않는다 — UI 는 열람·비교·실험용으로 쓰고,
확정된 변경은 YAML 에 반영한 뒤 다시 올린다.

    # 전부 올리기 (defaults.yaml 의 활성 버전에는 production 라벨도 붙는다)
    .\.venv\Scripts\python.exe scripts\sync_prompts.py

    # 특정 노드만
    .\.venv\Scripts\python.exe scripts\sync_prompts.py --node vision

    # 무엇이 올라갈지만 확인
    .\.venv\Scripts\python.exe scripts\sync_prompts.py --dry-run

올라간 뒤에는 ``.env`` 에서 ``PROMPT_SOURCE=langfuse`` 로 바꾸면 런타임이 Langfuse 를
읽는다. 실패하면 파일로 자동 폴백하므로 시연 중 Langfuse 가 죽어도 서비스는 산다.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import prompts  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.prompts import langfuse_source  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="로컬 프롬프트 YAML 을 Langfuse 로 올린다.")
    parser.add_argument("--node", action="append", help="올릴 노드 (여러 번 지정 가능). 비우면 전부")
    parser.add_argument("--version", help="특정 버전만. 비우면 그 노드의 모든 버전")
    parser.add_argument("--dry-run", action="store_true", help="올리지 않고 목록만 출력")
    args = parser.parse_args()

    settings = get_settings()
    nodes = args.node or prompts.node_names()

    plan: list[tuple[str, str, bool]] = []
    for node in nodes:
        versions = [args.version] if args.version else prompts.available(node)
        if not versions:
            print(f"!! {node}: 버전 파일이 없다")
            return 1
        active = prompts.resolve_version(node)
        for version in versions:
            plan.append((node, version, version == active))

    print(f"대상 {len(plan)}건 · Langfuse {settings.langfuse_base_url}"
          f" · prefix '{settings.langfuse_prompt_prefix}'\n")
    for node, version, production in plan:
        mark = "  ← production" if production else ""
        print(f"  {langfuse_source.prompt_name(node):28s} {version}{mark}")

    if args.dry_run:
        print("\n--dry-run 이라 올리지 않았다.")
        return 0

    if not settings.langfuse_ready:
        print("\n!! LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY 가 비어 있다. .env 를 채울 것.")
        return 1

    print()
    failed = 0
    for node, version, production in plan:
        data = prompts.read_file(node, version)
        try:
            created = langfuse_source.push(node, version, data, production=production)
        except Exception as exc:  # noqa: BLE001 - 어느 항목이 실패했는지 다 보여주고 끝낸다
            failed += 1
            print(f"  실패 {node}/{version}: {exc}")
            continue
        labels = [version] + (["production"] if production else [])
        print(f"  올림 {node}/{version} → langfuse v{getattr(created, 'version', '?')} "
              f"labels={labels}")

    if failed:
        print(f"\n{failed}건 실패.")
        return 1

    print(f"\n{len(plan)}건 완료. .env 에 PROMPT_SOURCE=langfuse 를 넣으면 런타임이 여기서 읽는다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
