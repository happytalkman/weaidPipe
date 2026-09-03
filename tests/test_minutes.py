"""core.minutes 단위 테스트."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import core.minutes as minutes


def _turns():
    return [
        {"seq": 1, "speaker": "user", "text": "신제품 출시 일정을 논의합시다.", "ts": ""},
        {"seq": 2, "speaker": "assistant", "text": "9월 말 출시로 잡는 게 좋겠습니다.", "ts": ""},
        {"seq": 3, "speaker": "user", "text": "좋아요. 마케팅은 누가 담당하죠?", "ts": ""},
        {"seq": 4, "speaker": "assistant", "text": "마케팅팀이 담당하고 예산안을 만들겠습니다.", "ts": ""},
    ]


class TestBuildPrompt(unittest.TestCase):
    def test_prompt_contains_turns_and_speakers(self):
        p = minutes.build_minutes_prompt(_turns(), ["홍길동"])
        self.assertIn("홍길동", p)
        self.assertIn("신제품 출시", p)
        self.assertIn("AID", p)

    def test_prompt_default_speaker(self):
        p = minutes.build_minutes_prompt(_turns())
        self.assertIn("참석자", p)


class TestParseMinutes(unittest.TestCase):
    def test_parse_full(self):
        data = {
            "title": "신제품 회의",
            "attendees": ["홍길동"],
            "agenda": ["출시 일정"],
            "discussion": ["9월 말 출시"],
            "decisions": ["9월 말 확정"],
            "action_items": [{"owner": "마케팅팀", "task": "예산안 작성"}],
            "next_meeting": "다음 주 화요일",
        }
        m = minutes.parse_minutes(data)
        self.assertEqual(m["title"], "신제품 회의")
        self.assertEqual(m["action_items"][0]["owner"], "마케팅팀")

    def test_parse_minimal(self):
        m = minutes.parse_minutes({"title": ""})
        self.assertEqual(m["title"], "회의록")
        self.assertEqual(m["action_items"], [])

    def test_parse_none(self):
        self.assertIsNone(minutes.parse_minutes("not a dict"))


class TestLocalMinutes(unittest.TestCase):
    def test_local_includes_content(self):
        md = minutes.local_minutes(_turns(), "주간 회의")
        self.assertIn("# 주간 회의", md)
        self.assertIn("신제품 출시", md)
        self.assertIn("## 논의 내용", md)
        self.assertIn("## 액션 아이템", md)

    def test_markdown_sections(self):
        m = minutes.parse_minutes({
            "title": "T", "attendees": ["A"], "agenda": ["B"],
            "discussion": ["C"], "decisions": ["D"],
            "action_items": [{"owner": "E", "task": "F"}], "next_meeting": "G",
        })
        md = minutes.minutes_to_markdown(m)
        for section in ("## 참석자", "## 안건", "## 논의 내용", "## 결정 사항", "## 액션 아이템", "## 다음 회의"):
            self.assertIn(section, md)


class TestWriteMinutes(unittest.TestCase):
    def test_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = minutes.write_minutes("# test", Path(tmp) / "m.md")
            self.assertTrue(out.exists())
            self.assertEqual(out.read_text(encoding="utf-8"), "# test")


class TestCommandDetection(unittest.TestCase):
    def test_detect(self):
        self.assertTrue(minutes.extract_minutes_command("회의록 작성해줘"))
        self.assertTrue(minutes.extract_minutes_command("회의 내용 정리해줘"))

    def test_not_detect(self):
        self.assertFalse(minutes.extract_minutes_command("회의가 언제야?"))
        self.assertFalse(minutes.extract_minutes_command("오늘 날씨 알려줘"))


if __name__ == "__main__":
    unittest.main()
