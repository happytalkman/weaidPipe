"""A1/A2 단위 테스트: single_instance / crashlog."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import core.crashlog as crashlog
import core.single_instance as si


class TestSingleInstance(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old = si.LOCK_FILE
        si.LOCK_FILE = Path(self._tmp.name) / ".lock"

    def tearDown(self):
        si.LOCK_FILE = self._old
        self._tmp.cleanup()

    def test_acquire_when_free(self):
        self.assertTrue(si.acquire(pid_alive=lambda pid: False))
        self.assertTrue(si.LOCK_FILE.exists())

    def test_acquire_when_alive(self):
        # 기존 PID가 살아 있으면 거부
        si.LOCK_FILE.write_text("12345", encoding="utf-8")
        self.assertFalse(si.acquire(pid_alive=lambda pid: True))

    def test_acquire_when_dead_pid(self):
        # 기존 PID가 죽어 있으면 새로 획득
        si.LOCK_FILE.write_text("99999", encoding="utf-8")
        self.assertTrue(si.acquire(pid_alive=lambda pid: False))

    def test_release(self):
        si.acquire(pid_alive=lambda pid: False)
        si.release()
        self.assertFalse(si.LOCK_FILE.exists())

    def test_is_pid_alive_self(self):
        import os
        self.assertTrue(si.is_pid_alive(os.getpid()))
        self.assertFalse(si.is_pid_alive(99999999))


class TestCloseBehavior(unittest.TestCase):
    def test_tray_when_available(self):
        self.assertEqual(si.close_behavior({"close_behavior": "tray"}, True), "tray")

    def test_quit_setting(self):
        self.assertEqual(si.close_behavior({"close_behavior": "quit"}, True), "quit")

    def test_quit_when_no_tray(self):
        self.assertEqual(si.close_behavior({"close_behavior": "tray"}, False), "quit")

    def test_default_is_tray(self):
        self.assertEqual(si.close_behavior({}, True), "tray")


class TestCrashLog(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old = crashlog.CRASH_LOG
        crashlog.CRASH_LOG = Path(self._tmp.name) / "crash.log"

    def tearDown(self):
        crashlog.CRASH_LOG = self._old
        self._tmp.cleanup()

    def test_format(self):
        line = crashlog.format_crash("2026-08-19T12:00:00", "test", "boom")
        self.assertIn("[test]", line)
        self.assertIn("boom", line)
        self.assertTrue(line.endswith("\n"))

    def test_append_and_read(self):
        crashlog.append_crash("test", "first")
        crashlog.append_crash("test", "second")
        last = crashlog.last_crash()
        self.assertIn("first", last)
        self.assertIn("second", last)

    def test_last_crash_empty(self):
        self.assertEqual(crashlog.last_crash(), "")


if __name__ == "__main__":
    unittest.main()
