"""core.reporter 단위 테스트."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import core.reporter as reporter


def _store():
    return {
        "turns": [{"seq": i, "speaker": "user" if i % 2 else "assistant", "text": "x"} for i in range(1, 21)],
        "topics": [
            {"label": "인공지능", "layer": "semantic"},
            {"label": "학습", "layer": "kinetic"},
        ],
        "triples": [
            {"subject": "학습", "predicate": "향상시킨다", "object": "성능", "layer": "dynamic"},
        ],
        "entities": [],
        "relations": [
            {"source": "학습", "target": "성능", "label": "이끈다", "kind": "causal", "layer": "dynamic"},
            {"source": "a", "target": "b", "label": "x", "kind": "associative", "layer": "semantic"},
        ],
        "summaries": [{"summary": "요약 내용"}],
    }


class TestBuildContext(unittest.TestCase):
    def test_context_sections(self):
        ctx = reporter.build_report_context(_store())
        self.assertIn("주제:", ctx)
        self.assertIn("S-P-O 트리플", ctx)
        self.assertIn("인과관계", ctx)
        self.assertIn("학습 → 성능", ctx)
        self.assertIn("요약 내용", ctx)
        self.assertNotIn("associative 관계", ctx.replace("인과관계", ""))

    def test_context_excludes_non_causal_relations(self):
        ctx = reporter.build_report_context(_store())
        # 인과 섹션에는 associative 관계가 없어야 한다
        causal_section = ctx.split("인과관계:")[1].split("대화 요약")[0]
        self.assertNotIn("a → b", causal_section)


class TestParseReport(unittest.TestCase):
    def test_parse_full(self):
        r = reporter.parse_report({
            "title": "AI 분석",
            "summary": "요약",
            "key_findings": ["발견1"],
            "topics": ["주제1"],
            "causal_insights": ["원인1"],
            "recommendations": ["제언1"],
        })
        self.assertEqual(r["title"], "AI 분석")
        self.assertEqual(r["key_findings"], ["발견1"])

    def test_parse_minimal(self):
        r = reporter.parse_report({})
        self.assertEqual(r["title"], "대화 분석 보고서")

    def test_parse_none(self):
        self.assertIsNone(reporter.parse_report("bad"))


class TestLocalReport(unittest.TestCase):
    def test_local_sections(self):
        md = reporter.local_report(_store(), "테스트 보고서")
        self.assertIn("# 테스트 보고서", md)
        self.assertIn("## 개요", md)
        self.assertIn("## 주요 주제", md)
        self.assertIn("## 인과 인사이트", md)
        self.assertIn("학습 → 성능", md)
        self.assertIn("## 대화 요약", md)


class TestMarkdown(unittest.TestCase):
    def test_sections(self):
        r = reporter.parse_report({
            "title": "T", "summary": "S", "key_findings": ["K"],
            "topics": ["P"], "causal_insights": ["C"], "recommendations": ["R"],
        })
        md = reporter.report_to_markdown(r)
        for s in ("## 요약", "## 핵심 발견", "## 주요 주제", "## 인과 인사이트", "## 제언"):
            self.assertIn(s, md)


class TestWrite(unittest.TestCase):
    def test_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = reporter.write_report("# r", Path(tmp) / "r.md")
            self.assertTrue(out.exists())


class TestCommandDetection(unittest.TestCase):
    def test_detect(self):
        self.assertTrue(reporter.extract_report_command("보고서 작성해줘"))
        self.assertTrue(reporter.extract_report_command("분석 문서 만들어줘"))

    def test_not_detect(self):
        self.assertFalse(reporter.extract_report_command("보고서가 뭐야?"))
        self.assertFalse(reporter.extract_report_command("오늘 날씨"))


if __name__ == "__main__":
    unittest.main()
