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

    def test_stops_after_silence(self):
        stream = self._FakeStream()
        samples = gv.record_until_silence(lambda: stream, sample_rate=16000, silence_seconds=0.06, max_seconds=5.0)
        self.assertGreater(len(samples), 0)
        self.assertLess(stream.calls, 20)  # 무음 감지로 조기 종료

    def test_max_duration_caps(self):
        class _LoudStream:
            def read_block(self):
                import numpy as np
                return (np.sin(np.linspace(0, 10, 1024)) * 0.3).astype("float32")
        samples = gv.record_until_silence(lambda: _LoudStream(), sample_rate=16000, silence_seconds=0.5, max_seconds=0.2)
        # 0.2초 = 약 3블록(1024/16000=0.064s)
        self.assertGreater(len(samples), 0)
        self.assertLess(len(samples), 16000 * 0.5)


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


if __name__ == "__main__":
    unittest.main()
