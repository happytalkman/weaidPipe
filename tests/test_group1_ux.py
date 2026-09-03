"""그룹 1 (A1~A4) 단위 테스트: settings / notify / applog / audio_health."""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import core.applog as applog
import core.audio_health as audio_health
import core.notify as notify
import core.settings as settings


class TestSettings(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old = settings.SETTINGS_FILE
        settings.SETTINGS_FILE = Path(self._tmp.name) / "user_settings.json"

    def tearDown(self):
        settings.SETTINGS_FILE = self._old
        self._tmp.cleanup()

    def test_defaults(self):
        s = settings.load_settings()
        self.assertEqual(s["voice_name"], "puck")
        self.assertEqual(s["summary_every_turns"], 10)

    def test_set_and_get(self):
        settings.set_setting("voice_name", "kore")
        self.assertEqual(settings.get("voice_name"), "kore")

    def test_bool_coercion(self):
        settings.set_setting("notifications_on", "false")
        self.assertFalse(settings.get("notifications_on"))

    def test_invalid_int_falls_back(self):
        settings.set_setting("proactive_interval", -5)
        self.assertEqual(settings.get("proactive_interval"), 30)

    def test_unknown_key_ignored(self):
        before = settings.load_settings()
        settings.set_setting("no_such_key", 1)
        self.assertEqual(settings.load_settings(), before)

    def test_env_updates(self):
        settings.set_setting("voice_name", "aoede")
        updates = settings.as_env_updates()
        self.assertEqual(updates["GEMINI_VOICE_NAME"], "aoede")


class TestNotifyPure(unittest.TestCase):
    def test_sanitize(self):
        self.assertEqual(notify.sanitize_message("  hi  "), "hi")
        self.assertEqual(notify.sanitize_message("a'quote"), "a''quote")
        self.assertEqual(notify.sanitize_message("x" * 500), "x" * 200)
        self.assertEqual(notify.sanitize_message(""), "")

    def test_rate_limiter(self):
        rl = notify.RateLimiter(cooldown_seconds=60)
        self.assertTrue(rl.allow("alarm", now=1000.0))
        self.assertFalse(rl.allow("alarm", now=1001.0))
        self.assertTrue(rl.allow("alarm", now=1061.0))
        self.assertTrue(rl.allow("other", now=1001.0))

    def test_rate_limiter_reset(self):
        rl = notify.RateLimiter(60)
        rl.allow("x", now=0.0)
        rl.reset("x")
        self.assertTrue(rl.allow("x", now=10.0))


class TestAppLog(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_dir = applog.LOG_DIR
        self._old_err = applog.ERRORS_FILE
        applog.LOG_DIR = Path(self._tmp.name)
        applog.ERRORS_FILE = Path(self._tmp.name) / "recent_errors.json"

    def tearDown(self):
        applog.LOG_DIR = self._old_dir
        applog.ERRORS_FILE = self._old_err
        self._tmp.cleanup()

    def test_log_path_for(self):
        day = datetime(2026, 8, 19)
        self.assertEqual(applog.log_path_for(day).name, "app-20260819.log")

    def test_log_write(self):
        path = applog.log_write("hello", "INFO")
        self.assertTrue(path.exists())
        self.assertIn("hello", path.read_text(encoding="utf-8"))

    def test_prune(self):
        for i in range(5, 20):
            day = datetime(2026, 8, 1) + timedelta(days=i)
            applog.log_path_for(day).write_text("x", encoding="utf-8")
        removed = applog.prune_old_logs(now=datetime(2026, 8, 25), keep_days=14)
        self.assertEqual(removed, 5)

    def test_error_collector(self):
        applog.ErrorCollector.record("boom", "test")
        applog.ErrorCollector.record("boom2", "test2")
        errors = applog.ErrorCollector.load()
        self.assertEqual(len(errors), 2)
        self.assertEqual(errors[-1]["message"], "boom2")
        applog.ErrorCollector.clear()
        self.assertEqual(applog.ErrorCollector.load(), [])


class TestAudioHealth(unittest.TestCase):
    def test_backoff(self):
        p = audio_health.RetryPolicy(base_seconds=10, max_seconds=300)
        p.on_failure(now=0.0)
        self.assertFalse(p.should_retry(now=5.0))
        self.assertTrue(p.should_retry(now=11.0))

    def test_exponential(self):
        p = audio_health.RetryPolicy(base_seconds=10)
        p.on_failure(now=0.0)
        p.on_failure(now=10.0)
        p.on_failure(now=30.0)
        self.assertAlmostEqual(p.next_delay(), 40.0)  # 10 * 2^(3-1)

    def test_max_cap(self):
        p = audio_health.RetryPolicy(base_seconds=10, max_seconds=60)
        for _ in range(10):
            p.on_failure()
        self.assertLessEqual(p.next_delay(), 60.0)

    def test_success_resets(self):
        p = audio_health.RetryPolicy(10)
        p.on_failure(now=0.0)
        p.on_success()
        self.assertFalse(p.should_retry(now=100.0))

    def test_state_names(self):
        self.assertEqual(audio_health.audio_state_name(True, False), "audio_ok")
        self.assertEqual(audio_health.audio_state_name(False, True), "audio_retrying")
        self.assertEqual(audio_health.audio_state_name(False, False), "audio_silent")


if __name__ == "__main__":
    unittest.main()
