"""core.bots 단위 테스트."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import core.bots as bots


class TestBotStore(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old = bots.BOTS_FILE
        bots.BOTS_FILE = Path(self._tmp.name) / "bots.json"

    def tearDown(self):
        bots.BOTS_FILE = self._old
        self._tmp.cleanup()

    def test_new_bot_defaults(self):
        b = bots.new_bot("분석가", "데이터 분석 전문가")
        self.assertEqual(b["provider"], "gemini")
        self.assertEqual(b["model"], "gemini-2.5-flash")
        self.assertEqual(b["memory"], [])

    def test_new_bot_grok(self):
        b = bots.new_bot("작가", "글쓰기", provider="grok", skills=["요약", "번역"])
        self.assertEqual(b["provider"], "grok")
        self.assertEqual(b["model"], "grok-4")
        self.assertEqual(len(b["skills"]), 2)

    def test_bad_provider_falls_back(self):
        b = bots.new_bot("x", "r", provider="unknown")
        self.assertEqual(b["provider"], "gemini")

    def test_get_delete(self):
        b = bots.new_bot("철수봇", "역할1")
        store = [b]
        self.assertIs(bots.get_bot(store, b["id"]), b)
        self.assertIs(bots.get_bot(store, "철수봇"), b)
        self.assertIsNone(bots.get_bot(store, "없는봇"))
        self.assertTrue(bots.delete_bot(store, b["id"]))
        self.assertEqual(store, [])
        self.assertFalse(bots.delete_bot(store, b["id"]))


class TestMemory(unittest.TestCase):
    def test_append_cap(self):
        b = bots.new_bot("m", "r")
        for i in range(30):
            bots.append_memory(b, "user", f"msg{i}", cap=10)
        self.assertEqual(len(b["memory"]), 10)
        self.assertEqual(b["memory"][-1]["text"], "msg29")


class TestBotReply(unittest.TestCase):
    def test_reply_with_fake_generator(self):
        b = bots.new_bot("계산기", "덧셈 전문가")

        def fake_gen(system, prompt, **kw):
            self.assertIn("덧셈 전문가", system)
            self.assertEqual(kw.get("provider"), "gemini")
            return "답: 3"

        text = bots.bot_reply(b, "1+2=?", generate_fn=fake_gen)
        self.assertEqual(text, "답: 3")
        # 메모리 자동 기록 확인
        self.assertEqual(len(b["memory"]), 2)
        self.assertEqual(b["memory"][0]["speaker"], "user")

    def test_reply_provider_passthrough(self):
        b = bots.new_bot("g", "r", provider="grok", model="grok-3")
        seen = {}

        def fake_gen(system, prompt, **kw):
            seen.update(kw)
            return "ok"

        bots.bot_reply(b, "hi", generate_fn=fake_gen)
        self.assertEqual(seen.get("provider"), "grok")
        self.assertEqual(seen.get("model"), "grok-3")


class TestBotDialogue(unittest.TestCase):
    def test_dialogue_turn_count(self):
        a = bots.new_bot("기획자", "기획")
        b = bots.new_bot("개발자", "개발")
        calls = []

        def fake_gen(system, prompt, **kw):
            calls.append(prompt[:10])
            return "의견입니다"

        transcript = bots.bot_dialogue(a, b, "AI 기능", max_turns=3, generate_fn=fake_gen)
        self.assertEqual(len(transcript), 6)  # 3턴 × 2봇
        self.assertEqual(len(calls), 6)
        self.assertEqual(transcript[0]["bot"], "기획자")
        self.assertEqual(transcript[1]["bot"], "개발자")
        # 서로의 메모리에 대화가 쌓인다
        self.assertGreaterEqual(len(a["memory"]), 3)
        self.assertGreaterEqual(len(b["memory"]), 3)

    def test_dialogue_min_turns(self):
        a = bots.new_bot("a", "r")
        b = bots.new_bot("b", "r")
        transcript = bots.bot_dialogue(a, b, "x", max_turns=0, generate_fn=lambda s, p, **kw: "y")
        self.assertEqual(len(transcript), 2)  # 최소 1턴 보장


class TestParseCommand(unittest.TestCase):
    def test_list(self):
        cmd, _ = bots.parse_bot_command("봇 목록 보여줘")
        self.assertEqual(cmd, "list")

    def test_create(self):
        cmd, p = bots.parse_bot_command("봇 만들어줘 이름:분석가 역할:데이터 전문가")
        self.assertEqual(cmd, "create")
        self.assertEqual(p["name"], "분석가")
        self.assertIn("데이터", p["role"])

    def test_ask(self):
        cmd, p = bots.parse_bot_command("봇 분석가에게 물어봐: 오늘 매출 어때?")
        self.assertEqual(cmd, "ask")
        self.assertEqual(p["bot"], "분석가")
        self.assertIn("매출", p["message"])

    def test_chat(self):
        cmd, p = bots.parse_bot_command("봇 기획자랑 봇 개발자 대화시켜줘 주제:신제품 아이디어")
        self.assertEqual(cmd, "chat")
        self.assertEqual(p["bot_a"], "기획자")
        self.assertEqual(p["bot_b"], "개발자")
        self.assertIn("아이디어", p["topic"])

    def test_delete(self):
        cmd, p = bots.parse_bot_command("봇 분석가 삭제해줘")
        self.assertEqual(cmd, "delete")
        self.assertEqual(p["key"], "분석가")

    def test_none(self):
        cmd, _ = bots.parse_bot_command("오늘 날씨 어때")
        self.assertIsNone(cmd)


if __name__ == "__main__":
    unittest.main()
