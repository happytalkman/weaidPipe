"""그룹 4 (D2~D3) 단위 테스트: trends / exporters.

D1(웹 탐색기)의 백엔드 로직은 graph_store.query(이미 테스트됨)를 재사용하고,
D4(모바일 반응형)는 웹 빌드/타입체크로 검증한다.
"""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import core.exporters as exporters
import core.trends as trends


def _store():
    return {
        "turns": [
            {"seq": 1, "speaker": "user", "text": "인공지능에 대해 알려줘", "ts": "2026-08-19T09:00:00"},
            {"seq": 2, "speaker": "assistant", "text": "인공지능은 기술입니다", "ts": "2026-08-19T09:00:05"},
            {"seq": 3, "speaker": "user", "text": "인공지능 윤리는?", "ts": "2026-08-19T11:00:00"},
            {"seq": 4, "speaker": "assistant", "text": "...", "ts": "2026-08-19T11:00:05"},
        ],
        "topics": [{"label": "인공지능", "layer": "semantic"}],
        "triples": [], "entities": [], "relations": [], "summaries": [],
    }


class TestTrends(unittest.TestCase):
    def test_bucket_key(self):
        ts = datetime(2026, 8, 19, 9, 30)
        self.assertEqual(trends.bucket_key(ts, "hour"), "2026-08-19T09")
        self.assertEqual(trends.bucket_key(ts, "day"), "2026-08-19")

    def test_topic_buckets(self):
        result = trends.topic_buckets(_store(), granularity="hour")
        buckets = {b["bucket"]: b for b in result}
        self.assertIn("2026-08-19T09", buckets)
        self.assertEqual(buckets["2026-08-19T09"]["topics"][0]["label"], "인공지능")
        self.assertEqual(buckets["2026-08-19T09"]["topics"][0]["count"], 1)
        self.assertIn("2026-08-19T11", buckets)

    def test_topic_trend_rising(self):
        # 시간이 갈수록 더 많이 언급 → rising
        store = {
            "turns": [
                {"seq": 1, "speaker": "user", "text": "인공지능", "ts": "2026-08-19T08:00:00"},
                {"seq": 2, "speaker": "user", "text": "인공지능", "ts": "2026-08-19T12:00:00"},
                {"seq": 3, "speaker": "user", "text": "인공지능", "ts": "2026-08-19T12:10:00"},
                {"seq": 4, "speaker": "user", "text": "인공지능", "ts": "2026-08-19T14:00:00"},
                {"seq": 5, "speaker": "user", "text": "인공지능", "ts": "2026-08-19T14:10:00"},
                {"seq": 6, "speaker": "user", "text": "인공지능", "ts": "2026-08-19T14:20:00"},
            ],
            "topics": [{"label": "인공지능", "layer": "semantic"}],
            "triples": [], "entities": [], "relations": [], "summaries": [],
        }
        t = trends.topic_trend(store, "인공지능")
        self.assertEqual(t["direction"], "rising")

    def test_topic_trend_falling(self):
        store = {
            "turns": [
                {"seq": 1, "speaker": "user", "text": "인공지능", "ts": "2026-08-19T08:00:00"},
                {"seq": 2, "speaker": "user", "text": "인공지능", "ts": "2026-08-19T09:00:00"},
                {"seq": 3, "speaker": "user", "text": "날씨", "ts": "2026-08-19T10:00:00"},
                {"seq": 4, "speaker": "user", "text": "날씨", "ts": "2026-08-19T11:00:00"},
            ],
            "topics": [{"label": "인공지능", "layer": "semantic"}],
            "triples": [], "entities": [], "relations": [], "summaries": [],
        }
        t = trends.topic_trend(store, "인공지능")
        self.assertEqual(t["direction"], "falling")

    def test_command_extraction(self):
        self.assertEqual(trends.extract_trend_command("인공지능의 트렌드 알려줘"), "인공지능")
        self.assertEqual(trends.extract_trend_command("최근 화두가 뭐야?"), "")
        self.assertIsNone(trends.extract_trend_command("오늘 날씨"))


class TestExporters(unittest.TestCase):
    def test_obsidian_frontmatter(self):
        md = exporters.to_obsidian("# 제목\n내용", "노트", ["AI", "회의"])
        self.assertTrue(md.startswith("---"))
        self.assertIn('title: "노트"', md)
        self.assertIn("tags: [AI, 회의]", md)
        self.assertIn("# 제목", md)

    def test_obsidian_wikilink(self):
        md = exporters.to_obsidian("참고: [[인공지능]]", "n")
        self.assertIn("[[인공지능]]", md)

    def test_notion_conversion(self):
        md = exporters.to_notion("참고: [[인공지능]]\n- [ ] 할 일")
        self.assertIn("인공지능", md)
        self.assertNotIn("[[", md)
        self.assertIn("- [ ] 할 일", md)

    def test_sync_to_obsidian(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = exporters.sync_to_obsidian("# 내용", "테스트 노트", vault_path=tmp)
            self.assertIsNotNone(out)
            self.assertTrue(out.exists())
            text = out.read_text(encoding="utf-8")
            self.assertIn("title: \"테스트 노트\"", text)

    def test_sync_without_vault(self):
        self.assertIsNone(exporters.sync_to_obsidian("# x", "n", vault_path=""))

    def test_slug(self):
        self.assertEqual(exporters._slug("안녕 하세요!"), "안녕-하세요")
        self.assertEqual(exporters._slug(""), "note")


if __name__ == "__main__":
    unittest.main()
