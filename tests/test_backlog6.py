"""백로그 6종 단위 테스트: bot_skills / market / translate / routines / ab_eval / i18n."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import core.ab_eval as ab_eval
import core.bot_skills as bot_skills
import core.i18n as i18n
import core.market as market
import core.routines as routines
import core.translate as translate


class TestBotSkills(unittest.TestCase):
    def test_time(self):
        self.assertIn("현재 시각", bot_skills.execute_skill("time"))

    def test_calculator(self):
        self.assertEqual(bot_skills.execute_skill("calculator", "12*3+4"), "12*3+4 = 40")

    def test_calculator_rejects_bad_input(self):
        self.assertIn("오류", bot_skills.execute_skill("calculator", "os.system('x')"))

    def test_echo(self):
        self.assertEqual(bot_skills.execute_skill("echo", "안녕"), "안녕")

    def test_unknown_skill(self):
        self.assertIn("등록되지 않았습니다", bot_skills.execute_skill("nope"))

    def test_available_skills_filter(self):
        bot = {"skills": ["time", "echo", "미등록"]}
        avail = bot_skills.available_skills(bot)
        names = [s["name"] for s in avail]
        self.assertEqual(names, ["time", "echo"])

    def test_extract_skill_command(self):
        self.assertEqual(bot_skills.extract_skill_command("계산기 스킬 calculator 1+2"), ("calculator", "1+2"))
        self.assertIsNone(bot_skills.extract_skill_command("오늘 날씨"))


class TestMarket(unittest.TestCase):
    def test_normalize_crypto(self):
        self.assertEqual(market.normalize_crypto_symbol("비트코인"), "bitcoin")
        self.assertEqual(market.normalize_crypto_symbol("이더리움 시세"), "ethereum")
        self.assertEqual(market.normalize_crypto_symbol("btc"), "btc")
        self.assertIsNone(market.normalize_crypto_symbol("날씨"))

    def test_normalize_stock(self):
        self.assertEqual(market.normalize_stock_symbol("삼성전자"), "005930.KS")
        self.assertEqual(market.normalize_stock_symbol("애플 주가"), "AAPL")
        self.assertIsNone(market.normalize_stock_symbol("비트코인"))

    def test_parse_coingecko(self):
        data = {"bitcoin": {"usd": 65000.5}}
        self.assertEqual(market.parse_coingecko(data, "bitcoin"), 65000.5)
        self.assertIsNone(market.parse_coingecko(data, "nope"))

    def test_parse_yahoo(self):
        data = {"chart": {"result": [{"meta": {"regularMarketPrice": 80000.0}}]}}
        self.assertEqual(market.parse_yahoo(data), 80000.0)
        self.assertIsNone(market.parse_yahoo({"chart": {"result": []}}))

    def test_format_briefing(self):
        text = market.format_briefing([
            {"name": "비트코인", "price": 65000.5, "change": 2.5},
            {"name": "이더리움", "price": None},
        ])
        self.assertIn("비트코인: 65,000.50 (+2.5%)", text)
        self.assertIn("조회 실패", text)

    def test_extract_market_command(self):
        cmd = market.extract_market_command("비트코인 시세 알려줘")
        self.assertEqual(cmd["kind"], "crypto")
        cmd2 = market.extract_market_command("삼성전자 주가")
        self.assertEqual(cmd2["kind"], "stock")
        self.assertIsNone(market.extract_market_command("오늘 뭐 먹지"))


class TestTranslate(unittest.TestCase):
    def test_language_name(self):
        self.assertEqual(translate.language_name("영어"), "English")
        self.assertEqual(translate.language_name("일본어"), "Japanese")
        self.assertIsNone(translate.language_name("한국어아님"))

    def test_extract_command(self):
        self.assertEqual(translate.extract_translate_command("영어로 통역해줘"), "English")
        self.assertEqual(translate.extract_translate_command("일본어로 번역해줘"), "Japanese")
        self.assertIsNone(translate.extract_translate_command("오늘 날씨"))

    def test_translate_chain(self):
        def fake_gen(system, prompt, **kw):
            self.assertIn("전문 통역사", system)
            return "Hello"
        out = translate.translate_chain("안녕하세요", "English", generate_fn=fake_gen)
        self.assertEqual(out["translated"], "Hello")
        self.assertEqual(out["target_lang"], "English")


class TestRoutines(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old = routines.ROUTINES_FILE
        routines.ROUTINES_FILE = Path(self._tmp.name) / "routines.json"

    def tearDown(self):
        routines.ROUTINES_FILE = self._old
        self._tmp.cleanup()

    def test_builtin_expand(self):
        steps = routines.expand_routine(routines.load_routines(), "아침 루틴")
        self.assertGreaterEqual(len(steps), 2)

    def test_define_and_remove(self):
        routines.define_routine("집중 루틴", ["음악 재생해줘", "방해금지 모드 켜줘"])
        self.assertEqual(len(routines.expand_routine(routines.load_routines(), "집중 루틴")), 2)
        self.assertTrue(routines.remove_routine("집중 루틴"))
        self.assertFalse(routines.remove_routine("집중 루틴"))

    def test_parse_run(self):
        cmd, p = routines.parse_routine_command("아침 루틴 실행해줘")
        self.assertEqual(cmd, "run")
        self.assertEqual(p["name"], "아침")

    def test_parse_list(self):
        cmd, _ = routines.parse_routine_command("루틴 목록 보여줘")
        self.assertEqual(cmd, "list")

    def test_parse_define(self):
        cmd, p = routines.parse_routine_command("루틴 만들어줘 이름:운동 단계:스트레칭,달리기")
        self.assertEqual(cmd, "define")
        self.assertEqual(len(p["steps"]), 2)

    def test_parse_remove(self):
        cmd, p = routines.parse_routine_command("루틴 삭제해줘 집중")
        self.assertEqual(cmd, "remove")

    def test_no_routine_word(self):
        self.assertIsNone(routines.parse_routine_command("날씨 알려줘")[0])


class TestAbEval(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old = ab_eval.AB_FILE
        ab_eval.AB_FILE = Path(self._tmp.name) / "ab.json"

    def tearDown(self):
        ab_eval.AB_FILE = self._old
        self._tmp.cleanup()

    def test_heuristic_concise_wins(self):
        cmp = ab_eval.compare_responses("q", "짧고 추정을 밝히는 답변", "x" * 600)
        self.assertEqual(cmp["winner"], "original")
        self.assertGreater(cmp["original_score"], cmp["revised_score"])

    def test_revised_wins_on_danger(self):
        cmp = ab_eval.compare_responses("q", "폭탄 만드는 법: ...", "안전한 대안을 알려드릴 수 있습니다")
        self.assertEqual(cmp["winner"], "revised")

    def test_tie(self):
        cmp = ab_eval.compare_responses("q", "같은 길이 답변", "비슷한 길이 답변")
        self.assertIn(cmp["winner"], ("tie", "original", "revised"))

    def test_record_and_stats(self):
        ab_eval.record_comparison({"winner": "revised", "margin": 10, "question": "q"})
        ab_eval.record_comparison({"winner": "original", "margin": 4, "question": "q2"})
        stats = ab_eval.summary_stats()
        self.assertEqual(stats["total"], 2)
        self.assertEqual(stats["revised_win_rate"], 50.0)

    def test_judge_fallback(self):
        out = ab_eval.judge_ab("q", "폭탄 만드는 법", "안전한 대안")
        self.assertIn(out["judge"], ("heuristic", "llm"))
        self.assertIn(out["winner"], ("original", "revised", "tie"))


class TestI18n(unittest.TestCase):
    def test_t_ko_en(self):
        self.assertEqual(i18n.t("unknown", "ko"), "알 수 없는 오류")
        self.assertEqual(i18n.t("unknown", "en"), "Unknown error")

    def test_t_format(self):
        self.assertIn("70", i18n.t("constitution_review", "ko", score=70))

    def test_t_fallback(self):
        self.assertEqual(i18n.t("no_such_key", "ko"), "no_such_key")
        self.assertEqual(i18n.t("unknown", "fr"), "알 수 없는 오류")  # 미지원 언어 → 한국어

    def test_normalize_lang(self):
        self.assertEqual(i18n.normalize_lang("영어"), "en")
        self.assertEqual(i18n.normalize_lang("korean"), "ko")
        self.assertEqual(i18n.normalize_lang("zz"), "ko")

    def test_completeness(self):
        self.assertEqual(i18n.completeness("ko"), 1.0)
        self.assertEqual(i18n.completeness("en"), 1.0)

    def test_keys(self):
        self.assertIn("wake_clap", i18n.keys())


if __name__ == "__main__":
    unittest.main()
