"""프로액티브 어시스턴트 (말하기 전에 먼저).

사용자가 말하지 않아도 주기적으로 상황을 점검하고, 마땅한 일이 있으면
먼저 알린다:

  * 예약된 작업 시간 도래   (core.scheduler)
  * 대화 그래프 요약 필요   (core.graph_store)
  * (확장 포인트) 시간/세션 기반 힌트

핵심 로직은 순수 함수 compute_proactive_items 로 분리해 단위 테스트한다.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Any, Callable


def compute_proactive_items(
    now: datetime,
    store: dict[str, Any],
    tasks: list[dict[str, Any]],
    last_announced: dict[str, str],
    cooldown_minutes: int = 30,
) -> list[dict[str, Any]]:
    """지금 알릴 프로액티브 항목 목록을 계산한다 (순수 함수)."""
    from core import graph_store, scheduler

    items: list[dict[str, Any]] = []

    def _fresh(item_id: str) -> bool:
        last = last_announced.get(item_id)
        if not last:
            return True
        try:
            last_dt = datetime.fromisoformat(last)
        except (TypeError, ValueError):
            return True
        return (now - last_dt).total_seconds() >= cooldown_minutes * 60

    for t in tasks:
        if scheduler.task_due(t, now) and _fresh(f"task:{t.get('id')}"):
            items.append({
                "type": "scheduled",
                "id": f"task:{t.get('id')}",
                "task_id": t.get("id"),
                "action_text": t.get("action", ""),
                "text": f"⏰ 예약된 작업 시간입니다: {t.get('action', '')}",
            })

    if store and graph_store.needs_summary(store) and _fresh("summary_hint"):
        items.append({
            "type": "hint",
            "id": "summary_hint",
            "text": "대화가 길어졌습니다. 요약 노드를 생성해 드릴까요?",
        })

    return items


class ProactiveEngine:
    """백그라운드 프로액티브 루프 (interval 초 간격 점검)."""

    def __init__(self, on_item: Callable[[dict[str, Any]], None], interval_seconds: int = 30):
        self.on_item = on_item
        self.interval = max(5, int(interval_seconds))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_announced: dict[str, str] = {}

    def start(self) -> bool:
        if self._thread is not None and self._thread.is_alive():
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="proactive")
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:
        from core import graph_store, scheduler

        while not self._stop.is_set():
            try:
                now = datetime.now()
                store = graph_store.load()
                tasks = scheduler.load_tasks()
                items = compute_proactive_items(now, store, tasks, self._last_announced)
                for item in items:
                    self._last_announced[item["id"]] = now.isoformat(timespec="seconds")
                    if item.get("type") == "scheduled" and item.get("task_id"):
                        scheduler.mark_run(item["task_id"], now)
                    cb = self.on_item
                    if cb is not None:
                        try:
                            cb(item)
                        except Exception:
                            pass
            except Exception:
                pass
            for _ in range(self.interval):
                if self._stop.is_set():
                    return
                time.sleep(1)
