"""B1/B3 단위 테스트: intent / chat_search."""
from __future__ import annotations

import unittest

import core.chat_search as chat_search
import core.intent as intent


class TestIntent(unittest.TestCase):
    def test_commands_detected(self):
        commands = [
            "봇 목록 보여줘",
            "그래프에서 인공지능 찾아줘",
            "홀로그램 켜줘",
            "10분 후에 알려줘",
            "회의록 작성해줘",
            "왜 성능이 향상됐어?",
            "아침 루틴 실행해줘",
            "영어로 통역해줘",
            "비트코인 시세 알려줘",
            "내 이름은 철수야",
            "새 대화 시작해줘",
        ]
        for c in commands:
            self.assertEqual(intent.classify(c), "command", f"실패: {c}")

    def test_chat_not_misdetected(self):
        chats = [
            "오늘 날씨 어때?",
            "인공지능이 뭔지 설명해줘",
            "요즘 무슨 노래가 좋아?",
            "점심 메뉴 추천해줘",
            "주말에 뭐 하면 좋을까?",
            "이 영화 재미있을까?",
        ]
        for c in chats:
            self.assertEqual(intent.classify(c), "chat", f"오탐: {c}")

    def test_tune_check_clean(self):
        self.assertEqual(intent.tune_check(list(intent.CHAT_EXAMPLES)), [])


class TestChatSearch(unittest.TestCase):
    def _store(self):
        return {
            "turns": [
                {"seq": 1, "speaker": "user", "text": "커피 머신을 사고 싶어요", "ts": ""},
                {"seq": 2, "speaker": "assistant", "text": "커피 머신 추천은 드롱기입니다", "ts": ""},
                {"seq": 3, "speaker": "user", "text": "내일 회의 일정은?", "ts": ""},
                {"seq": 4, "speaker": "assistant", "text": "내일 오전 10시 회의가 있습니다", "ts": ""},
            ],
        }

    def test_search_finds_turns(self):
        results = chat_search.search_turns(self._store(), "커피 머신")
        self.assertGreaterEqual(len(results), 1)
        self.assertIn("커피", results[0]["text"])

    def test_search_no_match(self):
        self.assertEqual(chat_search.search_turns(self._store(), "축구"), [])

    def test_extract_command(self):
        self.assertEqual(
            chat_search.extract_search_command("지난주에 커피 머신 뭐라고 했지?"),
            "커피 머신",
        )
        self.assertEqual(
            chat_search.extract_search_command("이전 대화에서 회의 일정 찾아줘"),
            "회의 일정",
        )
        self.assertIsNone(chat_search.extract_search_command("오늘 날씨 어때?"))

    def test_format_results(self):
        text = chat_search.format_results([])
        self.assertIn("찾지 못했습니다", text)
        text2 = chat_search.format_results([{"speaker": "user", "text": "커피"}])
        self.assertIn("커피", text2)


if __name__ == "__main__":
    unittest.main()
