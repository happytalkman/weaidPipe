"""core.diagnosis 단위 테스트."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import core.diagnosis as diag


class TestSummarizeReport(unittest.TestCase):
    def test_summarize(self):
        report = {
            "stamp": "20260819-120000",
            "apply": True,
            "issues": [
                {"check": "compile", "severity": "critical", "title": "a", "detail": "", "file": "", "line": 0, "line_text": "", "fix": "manual"},
                {"check": "branding", "severity": "low", "title": "b", "detail": "", "file": "", "line": 0, "line_text": "", "fix": "manual"},
            ],
            "fixed": [{"issue_title": "", "action": "patch", "ok": True, "note": "x"}],
            "ai_results": [{"issue_title": "", "action": "ai_patch", "ok": True, "note": "y"}],
            "evolution": {"applied": ["조항1"], "reason": "r"},
            "verify": {"check_compile": {"ok": True, "issues": []}},
        }
        s = diag.summarize_report(report)
        self.assertEqual(s["issue_count"], 2)
        self.assertEqual(s["critical"], 1)
        self.assertEqual(s["fixed_count"], 2)
        self.assertEqual(s["evolution_applied"], 1)
        self.assertTrue(s["verify_ok"])

    def test_summarize_empty(self):
        s = diag.summarize_report({})
        self.assertEqual(s["issue_count"], 0)
        self.assertEqual(s["fixed_count"], 0)
        self.assertEqual(s["evolution_applied"], 0)
        self.assertTrue(s["verify_ok"])


class TestLoadReports(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old = diag.REPORTS_DIR
        diag.REPORTS_DIR = Path(self._tmp.name)

    def tearDown(self):
        diag.REPORTS_DIR = self._old
        self._tmp.cleanup()

    def test_empty_dir(self):
        self.assertEqual(diag.load_reports(), [])

    def test_sorted_latest_first(self):
        for stamp in ("20260819-100000", "20260819-110000", "20260819-120000"):
            (diag.REPORTS_DIR / f"cycle-{stamp}.json").write_text(
                json.dumps({"stamp": stamp, "issues": []}), encoding="utf-8"
            )
        reports = diag.load_reports(limit=2)
        self.assertEqual(len(reports), 2)
        self.assertEqual(reports[0]["stamp"], "20260819-120000")

    def test_ignore_broken_files(self):
        (diag.REPORTS_DIR / "cycle-broken.json").write_text("{not json", encoding="utf-8")
        (diag.REPORTS_DIR / "cycle-ok.json").write_text(json.dumps({"stamp": "ok"}), encoding="utf-8")
        reports = diag.load_reports()
        self.assertEqual(len(reports), 1)


class TestDiagnosisStatus(unittest.TestCase):
    def test_status_shape(self):
        status = diag.diagnosis_status()
        for key in ("graph", "constitution", "rlaif", "engine"):
            self.assertIn(key, status)
        self.assertIn("turns", status["graph"])
        self.assertIn("amendments", status["constitution"])
        self.assertIn("total", status["rlaif"])
        self.assertIn("report_count", status["engine"])


if __name__ == "__main__":
    unittest.main()
