"""그룹 3 (B1~B4) 단위 테스트: long_memory / rag / agent_loop / tone."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import core.agent_loop as agent_loop
import core.long_memory as lm
import core.rag as rag
import core.tone as tone


class TestLongMemory(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old = lm.MEMORY_FILE
        lm.MEMORY_FILE = Path(self._tmp.name) / "mem.json"

    def tearDown(self):
        lm.MEMORY_FILE = self._old
        self._tmp.cleanup()

    def test_extract_candidates(self):
        cands = lm.extract_memory_candidates("", "나는 커피를 좋아해")
        self.assertIn("커피", cands[0])

    def test_extract_remember_command(self):
        cands = lm.extract_memory_candidates("기억해줘: 내 생일은 5월 1일이야", "")
        self.assertIn("내 생일은 5월 1일이야", cands)

    def test_add_dedup(self):
        entries = []
        lm.add_memory(entries, "커피 선호")
        lm.add_memory(entries, "커피 선호")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["count"], 2)

    def test_recall(self):
        entries = [
            {"text": "커피를 좋아함", "count": 1},
            {"text": "파이썬 개발자", "count": 3},
            {"text": "축구 관람", "count": 1},
        ]
        results = lm.recall(entries, "커피 취향이 뭐야?")
        self.assertEqual(results[0]["text"], "커피를 좋아함")
        results2 = lm.recall(entries, "개발")
        self.assertEqual(results2[0]["text"], "파이썬 개발자")

    def test_recall_empty_query(self):
        self.assertEqual(lm.recall([{"text": "x", "count": 1}], ""), [])

    def test_memory_context(self):
        entries = [{"text": "커피를 좋아함", "count": 1}]
        ctx = lm.memory_context(entries)
        self.assertIn("## 사용자 장기 기억", ctx)
        self.assertIn("커피를 좋아함", ctx)
        self.assertEqual(lm.memory_context([]), "")


class TestRag(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _make_docs(self):
        (self.dir / "a.txt").write_text("인공지능은 기계 학습 기술입니다.", encoding="utf-8")
        (self.dir / "b.md").write_text("커피 원두는 에티오피아에서 왔습니다.", encoding="utf-8")
        (self.dir / "c.pdf").write_text("pdf", encoding="utf-8")  # 미지원 확장자

    def test_load_documents(self):
        self._make_docs()
        docs = rag.load_documents(self.dir)
        self.assertEqual(len(docs), 2)

    def test_search(self):
        self._make_docs()
        docs = rag.load_documents(self.dir)
        results = rag.search("인공지능 기술", docs)
        self.assertEqual(len(results), 1)
        self.assertIn("a.txt", results[0]["path"])

    def test_search_no_match(self):
        self._make_docs()
        docs = rag.load_documents(self.dir)
        self.assertEqual(rag.search("축구", docs), [])

    def test_context_for(self):
        self._make_docs()
        docs = rag.load_documents(self.dir)
        ctx = rag.context_for("커피", docs)
        self.assertIn("문서 검색 결과", ctx)
        self.assertIn("커피", ctx)

    def test_command_extraction(self):
        self.assertEqual(rag.extract_rag_command("내 문서에서 인공지능 찾아줘"), "인공지능")
        self.assertIsNone(rag.extract_rag_command("오늘 날씨 어때"))


class TestAgentLoop(unittest.TestCase):
    def test_verify(self):
        self.assertTrue(agent_loop.verify_result("답: 서울의 날씨는 맑습니다", "서울 날씨"))
        self.assertFalse(agent_loop.verify_result("관련 정보 없음", "서울 날씨"))
        self.assertTrue(agent_loop.verify_result("결과 있음", ""))  # 목표 토큰 없으면 내용만 확인

    def test_should_continue(self):
        self.assertTrue(agent_loop.should_continue(1, 5, "중간 결과", "목표"))
        self.assertFalse(agent_loop.should_continue(5, 5, "중간 결과", "목표"))
        self.assertFalse(agent_loop.should_continue(1, 5, "답: 완료", "목표"))
        self.assertFalse(agent_loop.should_continue(1, 5, "", "목표"))

    def test_run_loop_completes(self):
        calls = []
        def executor(instruction):
            calls.append(instruction)
            return "답: 서울 날씨는 맑습니다"
        out = agent_loop.run_loop("서울 날씨", ["web_search"], executor)
        self.assertTrue(out["completed"])
        self.assertEqual(out["iterations"], 1)
        self.assertEqual(len(calls), 1)

    def test_run_loop_max_iterations(self):
        def executor(instruction):
            return "진행 중"
        out = agent_loop.run_loop("불가능한 목표", ["web_search"], executor, max_iterations=3)
        self.assertFalse(out["completed"])
        self.assertEqual(out["iterations"], 3)


class TestTone(unittest.TestCase):
    def test_urgent(self):
        self.assertEqual(tone.classify_tone({"rms": 0.4, "pitch": 220, "zero_cross": 0.06}), "urgent")

    def test_excited_energy(self):
        self.assertEqual(tone.classify_tone({"rms": 0.25, "pitch": 150, "zero_cross": 0.02}), "excited")

    def test_excited_pitch(self):
        self.assertEqual(tone.classify_tone({"rms": 0.1, "pitch": 250, "zero_cross": 0.02}), "excited")

    def test_calm(self):
        self.assertEqual(tone.classify_tone({"rms": 0.04, "pitch": 120, "zero_cross": 0.01}), "calm")

    def test_neutral(self):
        self.assertEqual(tone.classify_tone({"rms": 0.1, "pitch": 150, "zero_cross": 0.02}), "neutral")

    def test_guidance(self):
        self.assertIn("긴박", tone.tone_guidance("urgent"))
        self.assertEqual(tone.tone_guidance("neutral"), "")

    def test_history_average(self):
        self.assertEqual(tone.tone_history_average(["calm", "calm", "excited"]), "calm")
        self.assertEqual(tone.tone_history_average([]), "neutral")


if __name__ == "__main__":
    unittest.main()
