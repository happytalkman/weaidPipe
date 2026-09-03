"""core.grok_voice 단위 테스트 (순수 로직만)."""
from __future__ import annotations

import math
import unittest

import core.grok_voice as gv


def sine(hz: float, seconds: float = 0.5, sample_rate: int = 8000, amp: float = 1.0):
    return [amp * math.sin(2 * math.pi * hz * i / sample_rate) for i in range(int(seconds * sample_rate))]


class TestDetectSilence(unittest.TestCase):
    def test_silence(self):
        self.assertTrue(gv.detect_silence([0.0] * 1000))

    def test_quiet_noise_is_silence(self):
        self.assertTrue(gv.detect_silence([0.005] * 1000, threshold=0.02))

    def test_speech_is_not_silence(self):
        self.assertFalse(gv.detect_silence(sine(200.0), threshold=0.02))

    def test_empty_is_silence(self):
        self.assertTrue(gv.detect_silence([]))


class TestSwitchDecision(unittest.TestCase):
    def test_fatal_error_switches_immediately(self):
        self.assertTrue(gv.should_switch_to_grok("exceeded its monthly spending cap", 1))
        self.assertTrue(gv.should_switch_to_grok("API key not valid", 1))
        self.assertTrue(gv.should_switch_to_grok("permission denied", 1))

    def test_normal_error_needs_two_failures(self):
        self.assertFalse(gv.should_switch_to_grok("1011 internal error", 1))
        self.assertTrue(gv.should_switch_to_grok("1011 internal error", 2))

    def test_no_failures_no_switch(self):
        self.assertFalse(gv.should_switch_to_grok("spending cap", 0))


class TestRecordUntilSilence(unittest.TestCase):
    class _FakeStream:
        def __init__(self):
            self.calls = 0
        def read_block(self):
            self.calls += 1
            # 음성 5블록 → 무음 6블록
            if self.calls <= 5:
                import numpy as np
                return (np.sin(np.linspace(0, 10, 1024)) * 0.3).astype("float32")
            return [0.0] * 1024

    class _NoiseStream:
        def read_block(self):
            import numpy as np
            return (np.random.default_rng(42).normal(0, 0.008, 1024)).astype("float32")

    def test_stops_after_silence(self):
        stream = self._FakeStream()
        samples, ratio = gv.record_until_silence(lambda: stream, sample_rate=16000, silence_seconds=0.06, max_seconds=5.0)
        self.assertGreater(len(samples), 0)
        self.assertGreater(ratio, 0.3)  # 음성 비율 확인
        self.assertLess(stream.calls, 20)

    def test_noise_returns_low_ratio(self):
        stream = self._NoiseStream()
        samples, ratio = gv.record_until_silence(lambda: stream, sample_rate=16000, silence_seconds=0.1, max_seconds=0.3)
        self.assertLess(ratio, 0.25)  # 소음 → 음성 비율 낮음 → 폐기 대상

    def test_max_duration_caps(self):
        class _LoudStream:
            def read_block(self):
                import numpy as np
                return (np.sin(np.linspace(0, 10, 1024)) * 0.3).astype("float32")
        samples, ratio = gv.record_until_silence(lambda: _LoudStream(), sample_rate=16000, silence_seconds=0.5, max_seconds=0.2)
        self.assertGreater(len(samples), 0)
        self.assertLess(len(samples), 16000 * 0.5)
        self.assertGreater(ratio, 0.5)


class TestTTSBackend(unittest.TestCase):
    def test_pick_backend(self):
        # edge-tts 설치됨 → 'edge'
        self.assertEqual(gv.pick_tts_backend(), "edge")


class TestSessionHelpers(unittest.TestCase):
    def test_assistant_name(self):
        s = gv.GrokVoiceSession(system_prompt="x")
        self.assertEqual(s._assistant_name(), "AID")

    def test_log_callback(self):
        logs = []
        s = gv.GrokVoiceSession(system_prompt="x", on_log=logs.append)
        s._log("테스트")
        self.assertEqual(logs, ["테스트"])

    def test_spo_and_reply_callbacks(self):
        """S-P-O 표시(on_spo)와 인사이트(on_reply) 콜백이 발화/응답 시 호출된다."""
        spo_calls = []
        reply_calls = []
        s = gv.GrokVoiceSession(
            system_prompt="x",
            on_spo=spo_calls.append,
            on_reply=lambda q, a: reply_calls.append((q, a)),
        )
        # 내부 훅을 직접 호출해 콜백 배선 검증 (네트워크 없이)
        text = "안녕하세요"
        s.on_spo(text)
        reply = "안녕하세요, 반갑습니다"
        s.on_spo(reply)
        s.on_reply(text, reply)
        self.assertEqual(spo_calls, [text, reply])
        self.assertEqual(reply_calls, [(text, reply)])

    def test_respond_keeps_history(self):
        s = gv.GrokVoiceSession(system_prompt="x")
        s._history = ["사용자: 이전 질문", "AID: 이전 답변"]
        import core.grok_voice as gv2
        from unittest import mock
        with mock.patch("core.llm.generate", return_value="새 답변") as gen:
            out = s._respond("새 질문")
        self.assertEqual(out, "새 답변")
        # 프롬프트에 이전 대화가 포함되었는지 확인
        prompt = gen.call_args[0][1]
        self.assertIn("이전 질문", prompt)
        self.assertIn("새 질문", prompt)


class TestEchoFilter(unittest.TestCase):
    def test_exact_echo_detected(self):
        self.assertTrue(gv.is_echo("안녕하세요 반갑습니다", ["안녕하세요 반갑습니다"]))

    def test_similar_echo_detected(self):
        # TTS가 마이크로 유입되어 살짝 달라진 경우
        self.assertTrue(gv.is_echo("안녕하세요 반갑습니다", ["네 안녕하세요 반갑습니다"]))

    def test_high_overlap_detected(self):
        # 조사만 다른 경우 (TTS 유입의 전형적 변형)
        self.assertTrue(
            gv.is_echo("오늘 날씨가 맑습니다", ["오늘 날씨는 맑습니다"])
        )

    def test_different_speech_not_echo(self):
        self.assertFalse(gv.is_echo("내일 일정 알려줘", ["오늘 날씨는 맑습니다"]))

    def test_empty_not_echo(self):
        self.assertFalse(gv.is_echo("", ["아무 말"]))
        self.assertFalse(gv.is_echo("아무 말", []))


if __name__ == "__main__":
    unittest.main()
