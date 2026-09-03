from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.apps.a4_draft import _correct_missing_qa, _empty_qa_reflection, _validate_qa_reflection


class DraftQaReflectionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.prompt = SimpleNamespace(
            node="검증기",
            label="qa_reflection/v1",
            system="검증 규칙",
            schema={"type": "object"},
            temperature=0.0,
            render=lambda **values: f"{values['qa_history']}\n{values['draft_json']}",
        )

    def test_empty_answers_pass_without_missing_facts(self) -> None:
        self.assertEqual(
            _empty_qa_reflection(),
            {
                "passed": True,
                "coverage_score": 1.0,
                "missing_facts": [],
                "ignored_answers": [],
            },
        )

    @patch("app.apps.a4_draft.generate_structured")
    def test_validator_receives_free_text_answer_and_draft(self, generate) -> None:
        generate.return_value = {
            "passed": False,
            "coverage_score": 0.5,
            "missing_facts": ["어제 같은 위치에서 넘어질 뻔함"],
            "ignored_answers": [],
        }

        result = _validate_qa_reflection(
            self.prompt,
            "Q1. 이전에도 문제가 있었나요?\nA1. 어제 여기서 실제로 넘어질 뻔했어요",
            {"risk": "넘어질 위험이 있음."},
        )

        sent = generate.call_args.kwargs["user_prompt"]
        self.assertIn("실제로 넘어질 뻔했어요", sent)
        self.assertIn('"risk": "넘어질 위험이 있음."', sent)
        self.assertFalse(result["passed"])

    @patch("app.apps.a4_draft.generate_structured")
    def test_correction_contains_only_reported_missing_facts(self, generate) -> None:
        generate.return_value = {"risk": "보정됨"}
        draft_prompt = SimpleNamespace(
            node="문서생성기",
            label="draft/v3",
            schema={"type": "object"},
            temperature=0.3,
        )

        _correct_missing_qa(
            draft_prompt=draft_prompt,
            base_system_prompt="문체 규칙",
            base_user_prompt="원본 입력",
            draft={"risk": "기존 내용"},
            missing_facts=["통행량이 많은 장소임"],
        )

        sent = generate.call_args.kwargs["user_prompt"]
        self.assertIn("통행량이 많은 장소임", sent)
        self.assertIn(json.dumps({"risk": "기존 내용"}, ensure_ascii=False), sent)
        self.assertIn("사실을 과장하거나 새로 만들지 말고", sent)


if __name__ == "__main__":
    unittest.main()
