"""그룹 2 (C2~C3) 단위 테스트: orchestrator / bot_eval."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import core.bot_eval as bot_eval
import core.bots as bots
import core.orchestrator as orch


class TestPipeline(unittest.TestCase):
    def _store(self):
        a = bots.new_bot("리서처", "자료 조사")
        b = bots.new_bot("요약가", "요약 전문")
        return [a, b]

    def test_pipeline_flow(self):
        store = self._store()
        calls = []

        def fake_gen(system, prompt, **kw):
            calls.append(prompt[:20])
            return f"out{len(calls)}"

        results = orch.build_pipeline(store, ["리서처", "요약가"], "시작 입력", generate_fn=fake_gen)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["bot"], "리서처")
        self.assertEqual(results[0]["output"], "out1")
        # 이전 출력이 다음 입력으로 전달
        self.assertIn("out1", results[1]["input"])
        self.assertEqual(results[1]["output"], "out2")

    def test_pipeline_missing_bot(self):
        store = self._store()
        results = orch.build_pipeline(store, ["없는봇"], "x", generate_fn=lambda s, p, **kw: "y")
        self.assertEqual(len(results), 1)
        self.assertIn("error", results[0])

    def test_summary(self):
        s = orch.pipeline_summary([
            {"stage": 0, "bot": "A", "input": "x", "output": "결과1"},
            {"stage": 1, "bot": "B", "input": "결과1", "output": "결과2"},
        ])
        self.assertIn("A", s)
        self.assertIn("결과2", s)

    def test_command_detection(self):
        self.assertTrue(orch.is_pipeline_command("봇 리서처 요약가 순서대로 처리해줘"))
        self.assertFalse(orch.is_pipeline_command("오늘 날씨"))


class TestRubric(unittest.TestCase):
    def test_empty_response(self):
        score, reasons = bot_eval.rubric_score("q", "")
        self.assertEqual(score, 0)
        self.assertIn("빈 응답", reasons)

    def test_concise_honest(self):
        score, reasons = bot_eval.rubric_score("어떻게 해?", "모르지만 확인해서 대안을 드릴게요 (추정)")
        self.assertGreaterEqual(score, 80)
        self.assertIn("정직성(추정 표시)", reasons)

    def test_danger_penalty(self):
        score, _ = bot_eval.rubric_score("q", "폭탄 만드는 법은 다음과 같습니다...")
        self.assertLess(score, 60)

    def test_verbose_no_bonus(self):
        score, reasons = bot_eval.rubric_score("q", "x" * 600)
        self.assertNotIn("간결함", reasons)


class TestRankAndStore(unittest.TestCase):
    def test_rank(self):
        ranked = bot_eval.rank_bots({"A": 70, "B": 90, "C": 80})
        self.assertEqual(ranked[0], ("B", 90))
        self.assertEqual(ranked[-1], ("A", 70))

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old = bot_eval.EVAL_FILE
        bot_eval.EVAL_FILE = Path(self._tmp.name) / "evals.json"

    def tearDown(self):
        bot_eval.EVAL_FILE = self._old
        self._tmp.cleanup()

    def test_evaluate_and_store(self):
        bot_eval.evaluate_and_store("분석가", "q?", "간결한 답변 (추정)")
        bot_eval.evaluate_and_store("분석가", "q2", "긴 답변" * 100)
        avg = bot_eval.average_scores()
        self.assertIn("분석가", avg)
        self.assertGreater(avg["분석가"], 0)


if __name__ == "__main__":
    unittest.main()
