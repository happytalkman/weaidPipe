"""core.voice_profile 단위 테스트 (합성 사인파로 결정적 검증)."""
from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

import core.voice_profile as vp


def sine(hz: float, seconds: float = 0.5, sample_rate: int = 8000, amp: float = 1.0):
    n = int(seconds * sample_rate)
    return [amp * math.sin(2 * math.pi * hz * i / sample_rate) for i in range(n)]


class TestExtractFeatures(unittest.TestCase):
    def test_sine_pitch_200hz(self):
        f = vp.extract_features(sine(200.0), 8000)
        self.assertAlmostEqual(f["pitch"], 200.0, delta=12.0)

    def test_sine_pitch_150hz(self):
        f = vp.extract_features(sine(150.0), 8000)
        self.assertAlmostEqual(f["pitch"], 150.0, delta=10.0)

    def test_rms_amplitude(self):
        f = vp.extract_features(sine(220.0, amp=0.5), 8000)
        self.assertAlmostEqual(f["rms"], 0.5 / math.sqrt(2), delta=0.02)

    def test_too_short_returns_zeros(self):
        f = vp.extract_features([0.1, -0.1, 0.1], 8000)
        self.assertEqual(f, {"rms": 0.0, "pitch": 0.0, "zero_cross": 0.0})


class TestClassify(unittest.TestCase):
    def _profiles(self):
        p1 = vp.new_profile("철수", vp.extract_features(sine(150.0), 8000))
        p2 = vp.new_profile("영희", vp.extract_features(sine(300.0), 8000))
        return [p1, p2]

    def test_classify_known_speaker(self):
        profiles = self._profiles()
        f = vp.extract_features(sine(152.0), 8000)  # 철수와 가까움
        self.assertEqual(vp.classify(f, profiles), profiles[0]["id"])

    def test_classify_other_speaker(self):
        profiles = self._profiles()
        f = vp.extract_features(sine(298.0), 8000)  # 영희와 가까움
        self.assertEqual(vp.classify(f, profiles), profiles[1]["id"])

    def test_classify_unknown(self):
        profiles = self._profiles()
        f = vp.extract_features(sine(60.0, seconds=0.4), 8000)  # 둘 다 아님 (지원 범위 내 저음)
        self.assertIsNone(vp.classify(f, profiles))


class TestProfiles(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old = vp.PROFILES_FILE
        vp.PROFILES_FILE = Path(self._tmp.name) / "profiles.json"

    def tearDown(self):
        vp.PROFILES_FILE = self._old
        self._tmp.cleanup()

    def test_update_converges(self):
        p = vp.new_profile()
        for _ in range(5):
            vp.update_profile(p, vp.extract_features(sine(200.0), 8000))
        self.assertEqual(p["samples"], 5)
        self.assertAlmostEqual(p["stats"]["pitch"], 200.0, delta=12.0)

    def test_save_load(self):
        profiles = [vp.new_profile("테스터", vp.extract_features(sine(180.0), 8000))]
        vp.save_profiles(profiles)
        loaded = vp.load_profiles()
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["name"], "테스터")

    def test_name_profile(self):
        p = vp.new_profile()
        self.assertTrue(vp.name_profile([p], p["id"], "새이름"))
        self.assertEqual(p["name"], "새이름")
        self.assertFalse(vp.name_profile([p], "nope", "x"))


if __name__ == "__main__":
    unittest.main()
