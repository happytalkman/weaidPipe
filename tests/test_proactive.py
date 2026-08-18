"""core.proactive 단위 테스트."""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import core.proactive as pro
import core.scheduler as sch


def _store(pairs=12):
    return {
        "turns": [{"seq": i, "speaker": "user" if i % 2 else "assistant", "text": "x"} for i in range(1, pairs * 2 + 1)],
        "topics": [], "triples": [], "entities": [], "relations": [], "summaries": [],
    }


def _task(task_id, spec):
    return {"id": task_id, "spec": spec, "action": "뉴스 요약", "created": "2026-01-01T00:00:00", "last_run": None}


class TestComputeItems(unittest.TestCase):
    def test_scheduled_due(self):
        now = datetime(2026, 8, 19, 9, 5, 0)  # 수요일
        tasks = [_task("t1", {"period": "daily", "hour": 9, "minute": 0})]
        items = pro.compute_proactive_items(now, _store(2), tasks, {})
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["type"], "scheduled")
        self.assertEqual(items[0]["task_id"], "t1")

    def test_scheduled_not_due(self):
        now = datetime(2026, 8, 19, 8, 50, 0)
        tasks = [_task("t1", {"period": "daily", "hour": 9, "minute": 0})]
        items = pro.compute_proactive_items(now, _store(2), tasks, {})
        self.assertEqual(items, [])

    def test_weekly_wrong_day(self):
        now = datetime(2026, 8, 19, 10, 5, 0)  # 수요일
        tasks = [_task("t1", {"period": "weekly", "weekday": 0, "hour": 10, "minute": 0})]  # 월요일
        items = pro.compute_proactive_items(now, _store(2), tasks, {})
        self.assertEqual(items, [])

    def test_cooldown(self):
        now = datetime(2026, 8, 19, 9, 5, 0)
        tasks = [_task("t1", {"period": "daily", "hour": 9, "minute": 0})]
        last = {"task:t1": now.isoformat()}
        items = pro.compute_proactive_items(now, _store(2), tasks, last)
        self.assertEqual(items, [])

    def test_cooldown_expired(self):
        now = datetime(2026, 8, 19, 9, 40, 0)
        tasks = [_task("t1", {"period": "daily", "hour": 9, "minute": 0})]
        last = {"task:t1": (now - timedelta(minutes=40)).isoformat()}
        items = pro.compute_proactive_items(now, _store(2), tasks, last)
        self.assertEqual(len(items), 1)

    def test_summary_hint(self):
        now = datetime(2026, 8, 19, 9, 5, 0)
        items = pro.compute_proactive_items(now, _store(12), [], {})  # 12턴, 요약 0 → 필요
        types = [i["type"] for i in items]
        self.assertIn("hint", types)

    def test_no_summary_hint_when_fresh(self):
        now = datetime(2026, 8, 19, 9, 5, 0)
        items = pro.compute_proactive_items(now, _store(4), [], {})  # 4턴 → 불필요
        types = [i["type"] for i in items]
        self.assertNotIn("hint", types)


class TestProactiveEngine(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old = sch.TASKS_FILE
        sch.TASKS_FILE = Path(self._tmp.name) / "tasks.json"

    def tearDown(self):
        sch.TASKS_FILE = self._old
        self._tmp.cleanup()

    def test_start_stop(self):
        got: list = []
        eng = pro.ProactiveEngine(on_item=got.append, interval_seconds=5)
        self.assertTrue(eng.start())
        self.assertTrue(eng.is_running())
        eng.stop()
        # 스레드 종료 대기
        import time
        for _ in range(50):
            if not eng.is_running():
                break
            time.sleep(0.1)
        self.assertFalse(eng.is_running())

    def test_engine_fires_due_task(self):
        sch.add_task({"period": "daily", "hour": 0, "minute": 0}, "브리핑")
        # last_run을 어제로 설정해 오늘 00:00 이후면 due
        tasks = sch.load_tasks()
        tasks[0]["last_run"] = (datetime.now() - timedelta(days=1)).isoformat()
        sch.save_tasks(tasks)
        got: list = []
        eng = pro.ProactiveEngine(on_item=got.append, interval_seconds=5)
        eng.start()
        import time
        for _ in range(80):
            if got:
                break
            time.sleep(0.1)
        eng.stop()
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["type"], "scheduled")


if __name__ == "__main__":
    unittest.main()
