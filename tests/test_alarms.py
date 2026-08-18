"""core.alarms 단위 테스트."""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import core.alarms as alarms


class TestParseDuration(unittest.TestCase):
    def test_minutes(self):
        self.assertEqual(alarms.parse_duration("10분 후에 알려줘"), 600)

    def test_hours_minutes(self):
        self.assertEqual(alarms.parse_duration("1시간 30분 타이머"), 5400)

    def test_seconds(self):
        self.assertEqual(alarms.parse_duration("90초 후에 알려줘"), 90)

    def test_none(self):
        self.assertIsNone(alarms.parse_duration("오늘 날씨 알려줘"))
        self.assertIsNone(alarms.parse_duration("내일 회의 있어?"))


class TestParseAlarmTime(unittest.TestCase):
    def test_afternoon(self):
        now = datetime(2026, 8, 19, 9, 0, 0)
        due = alarms.parse_alarm_time("오후 3시에 알람", now)
        self.assertEqual((due.hour, due.minute), (15, 0))
        self.assertEqual(due.day, now.day)

    def test_morning(self):
        now = datetime(2026, 8, 19, 9, 0, 0)
        due = alarms.parse_alarm_time("아침 8시 30분", now)
        self.assertEqual((due.hour, due.minute), (8, 30))

    def test_past_rolls_to_tomorrow(self):
        now = datetime(2026, 8, 19, 21, 0, 0)
        due = alarms.parse_alarm_time("오후 3시", now)
        self.assertEqual(due.day, now.day + 1)

    def test_none(self):
        self.assertIsNone(alarms.parse_alarm_time("10분 후에 알려줘"))


class TestAlarmStore(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old = alarms.ALARMS_FILE
        alarms.ALARMS_FILE = Path(self._tmp.name) / "alarms.json"

    def tearDown(self):
        alarms.ALARMS_FILE = self._old
        self._tmp.cleanup()

    def test_add_due_remove(self):
        self.assertEqual(alarms.load_alarms(), [])
        a = alarms.add_alarm(datetime.now() + timedelta(seconds=1), "테스트")
        self.assertEqual(len(alarms.load_alarms()), 1)
        self.assertIn("테스트", alarms.alarm_description(a))
        # 미래 알람은 due 아님
        self.assertEqual(alarms.due_alarms(datetime.now()), [])
        # 시간이 지나면 due
        later = datetime.now() + timedelta(seconds=5)
        due = alarms.due_alarms(later)
        self.assertEqual(len(due), 1)
        # fired 표시로 재발화 없음
        self.assertEqual(alarms.due_alarms(later), [])
        self.assertTrue(alarms.remove_alarm(a["id"]))
        self.assertFalse(alarms.remove_alarm(a["id"]))


class TestAlarmManager(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old = alarms.ALARMS_FILE
        alarms.ALARMS_FILE = Path(self._tmp.name) / "alarms.json"

    def tearDown(self):
        alarms.ALARMS_FILE = self._old
        self._tmp.cleanup()

    def test_start_stop(self):
        got: list = []
        mgr = alarms.AlarmManager(on_fire=got.append, interval_seconds=1)
        self.assertTrue(mgr.start())
        self.assertTrue(mgr.is_running())
        mgr.stop()
        import time
        for _ in range(30):
            if not mgr.is_running():
                break
            time.sleep(0.1)
        self.assertFalse(mgr.is_running())

    def test_fires_due_alarm(self):
        alarms.add_alarm(datetime.now(), "즉시 알람")
        got: list = []
        mgr = alarms.AlarmManager(on_fire=got.append, interval_seconds=1)
        mgr.start()
        import time
        for _ in range(40):
            if got:
                break
            time.sleep(0.1)
        mgr.stop()
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["label"], "즉시 알람")


if __name__ == "__main__":
    unittest.main()
