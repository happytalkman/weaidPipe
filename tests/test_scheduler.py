"""core.scheduler 단위 테스트."""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import core.scheduler as sch


class TestParseSchedule(unittest.TestCase):
    def test_daily_morning(self):
        spec = sch.parse_schedule("매일 아침 9시에 뉴스 요약해줘")
        self.assertEqual(spec, {"period": "daily", "hour": 9, "minute": 0})

    def test_daily_afternoon(self):
        spec = sch.parse_schedule("매일 오후 3시 30분에 알려줘")
        self.assertEqual(spec, {"period": "daily", "hour": 15, "minute": 30})

    def test_weekly(self):
        spec = sch.parse_schedule("매주 월요일 10시에 주간 보고 해줘")
        self.assertEqual(spec, {"period": "weekly", "weekday": 0, "hour": 10, "minute": 0})

    def test_weekly_evening(self):
        spec = sch.parse_schedule("매주 수요일 저녁 7시에 회의 준비")
        self.assertEqual(spec, {"period": "weekly", "weekday": 2, "hour": 19, "minute": 0})

    def test_no_schedule(self):
        self.assertIsNone(sch.parse_schedule("오늘 날씨 알려줘"))
        self.assertIsNone(sch.parse_schedule("내일 회의 있어?"))


class TestSplitAction(unittest.TestCase):
    def test_split(self):
        spec, action = sch.split_schedule_action("매일 아침 9시에 뉴스 요약해줘")
        self.assertEqual(spec["hour"], 9)
        self.assertEqual(action, "뉴스 요약해줘")

    def test_split_none(self):
        spec, action = sch.split_schedule_action("안녕하세요")
        self.assertIsNone(spec)
        self.assertEqual(action, "")


class TestTaskDue(unittest.TestCase):
    def _task(self, spec):
        return {"id": "t1", "spec": spec, "action": "테스트", "created": "2026-01-01T00:00:00", "last_run": None}

    def test_due_when_time_passed(self):
        now = datetime(2026, 8, 19, 9, 5, 0)
        self.assertTrue(sch.task_due(self._task({"period": "daily", "hour": 9, "minute": 0}), now))

    def test_not_due_before(self):
        now = datetime(2026, 8, 19, 8, 55, 0)
        self.assertFalse(sch.task_due(self._task({"period": "daily", "hour": 9, "minute": 0}), now))

    def test_weekly_wrong_day(self):
        # 2026-08-19는 수요일(2). 월요일(0) 태스크는 due 아님
        now = datetime(2026, 8, 19, 10, 0, 0)
        self.assertFalse(sch.task_due(self._task({"period": "weekly", "weekday": 0, "hour": 10, "minute": 0}), now))

    def test_weekly_right_day(self):
        now = datetime(2026, 8, 19, 10, 1, 0)  # 수요일
        self.assertTrue(sch.task_due(self._task({"period": "weekly", "weekday": 2, "hour": 10, "minute": 0}), now))

    def test_no_rerun_after_mark(self):
        now = datetime(2026, 8, 19, 9, 5, 0)
        task = self._task({"period": "daily", "hour": 9, "minute": 0})
        task["last_run"] = now.isoformat()
        self.assertFalse(sch.task_due(task, now))

    def test_bad_period(self):
        self.assertFalse(sch.task_due(self._task({"period": "once", "hour": 9}), datetime(2026, 8, 19, 9, 5)))


class TestTaskStore(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old = sch.TASKS_FILE
        sch.TASKS_FILE = Path(self._tmp.name) / "scheduled_tasks.json"

    def tearDown(self):
        sch.TASKS_FILE = self._old
        self._tmp.cleanup()

    def test_add_list_remove(self):
        self.assertEqual(sch.load_tasks(), [])
        t = sch.add_task({"period": "daily", "hour": 9, "minute": 0}, "뉴스 요약")
        self.assertEqual(len(sch.load_tasks()), 1)
        self.assertIn("매일 09:00", sch.task_description(t))
        self.assertTrue(sch.remove_task(t["id"]))
        self.assertFalse(sch.remove_task(t["id"]))
        self.assertEqual(sch.load_tasks(), [])

    def test_due_tasks_marks_run(self):
        sch.add_task({"period": "daily", "hour": 9, "minute": 0}, "브리핑")
        now = datetime(2026, 8, 19, 9, 1, 0)
        due = sch.due_tasks(now)
        self.assertEqual(len(due), 1)
        due2 = sch.due_tasks(now)
        self.assertEqual(due2, [])  # last_run 갱신으로 재실행 없음


class TestParseCommand(unittest.TestCase):
    def test_add(self):
        cmd, payload = sch.parse_command("매일 아침 8시에 날씨 알려줘")
        self.assertEqual(cmd, "add")
        self.assertEqual(payload["spec"]["hour"], 8)
        self.assertIn("날씨", payload["action"])

    def test_list(self):
        cmd, _ = sch.parse_command("예약 목록 보여줘")
        self.assertEqual(cmd, "list")

    def test_remove(self):
        cmd, payload = sch.parse_command("예약 취소해줘 ab12cd34")
        self.assertEqual(cmd, "remove")
        self.assertEqual(payload["task_id"], "ab12cd34")

    def test_none(self):
        cmd, _ = sch.parse_command("오늘 점심 뭐 먹지")
        self.assertIsNone(cmd)


if __name__ == "__main__":
    unittest.main()
