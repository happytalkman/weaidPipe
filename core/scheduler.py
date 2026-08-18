"""스케줄드 태스크 (반복 자동화).

"매일 아침 9시에 뉴스 요약해줘", "매주 월요일 10시에 주간 보고 해줘" 같은
반복 작업을 저장하고, 시간이 되면 실행 대상으로 반환한다.

저장: memory/scheduled_tasks.json (id, spec, action, created, last_run)
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
TASKS_FILE = BASE_DIR / "memory" / "scheduled_tasks.json"

_WEEKDAYS = {"월": 0, "화": 1, "수": 2, "목": 3, "금": 4, "토": 5, "일": 6}
_WEEKDAY_NAMES = {v: k for k, v in _WEEKDAYS.items()}

_DAILY_RE = re.compile(r"매일\s*(?:아침|오전|오후|저녁|밤)?\s*(\d{1,2})시(?:\s*(\d{1,2})분)?")
_WEEKLY_RE = re.compile(
    r"매주\s*([월화수목금토일])요일\s*(?:아침|오전|오후|저녁|밤)?\s*(\d{1,2})시(?:\s*(\d{1,2})분)?"
)
_AFTERNOON_MARK = ("오후", "저녁", "밤")


def _apply_meridiem(text: str, hour: int) -> int:
    if any(m in text for m in _AFTERNOON_MARK) and hour < 12:
        return hour + 12
    return hour


def parse_schedule(text: str) -> dict[str, Any] | None:
    """일정 표현에서 스펙을 추출한다. 없으면 None."""
    t = str(text or "")
    m = _WEEKLY_RE.search(t)
    if m:
        hour = _apply_meridiem(t, int(m.group(2)))
        minute = int(m.group(3) or 0)
        return {"period": "weekly", "weekday": _WEEKDAYS[m.group(1)], "hour": hour, "minute": minute}
    m = _DAILY_RE.search(t)
    if m:
        hour = _apply_meridiem(t, int(m.group(1)))
        minute = int(m.group(2) or 0)
        return {"period": "daily", "hour": hour, "minute": minute}
    return None


def split_schedule_action(text: str) -> tuple[dict[str, Any] | None, str]:
    """(스펙, 실행 내용) 분리. 스펙이 없으면 (None, '')."""
    spec = parse_schedule(text)
    if not spec:
        return None, ""
    action = _WEEKLY_RE.sub("", str(text))
    action = _DAILY_RE.sub("", action)
    action = re.sub(r"^(?:을|를|은|는|에|에는|에선)\s*", "", action).strip()
    action = action.rstrip(".").strip()
    return spec, action


def load_tasks() -> list[dict[str, Any]]:
    try:
        if TASKS_FILE.exists():
            data = json.loads(TASKS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
    except Exception:
        pass
    return []


def save_tasks(tasks: list[dict[str, Any]]) -> Path:
    TASKS_FILE.parent.mkdir(parents=True, exist_ok=True)
    TASKS_FILE.write_text(json.dumps(tasks, ensure_ascii=False, indent=2), encoding="utf-8")
    return TASKS_FILE


def add_task(spec: dict[str, Any], action: str) -> dict[str, Any]:
    """새 예약 작업을 저장한다."""
    task: dict[str, Any] = {
        "id": uuid.uuid4().hex[:8],
        "spec": spec,
        "action": (action or "").strip()[:400],
        "created": datetime.now().isoformat(timespec="seconds"),
        "last_run": None,
    }
    tasks = load_tasks()
    tasks.append(task)
    save_tasks(tasks)
    return task


def remove_task(task_id: str) -> bool:
    tasks = load_tasks()
    remaining = [t for t in tasks if t.get("id") != task_id]
    if len(remaining) == len(tasks):
        return False
    save_tasks(remaining)
    return True


def task_due(task: dict[str, Any], now: datetime) -> bool:
    """지금 실행할 시간이 됐는지 (해당 요일/시각 이후 & last_run 이전 아님)."""
    spec = task.get("spec") or {}
    period = spec.get("period")
    if period == "weekly":
        if now.weekday() != int(spec.get("weekday", 0)):
            return False
    elif period != "daily":
        return False
    due = now.replace(
        hour=int(spec.get("hour", 0)),
        minute=int(spec.get("minute", 0)),
        second=0,
        microsecond=0,
    )
    if now < due:
        return False
    last = task.get("last_run")
    if last:
        try:
            last_dt = datetime.fromisoformat(last)
        except (TypeError, ValueError):
            last_dt = None
        if last_dt is not None and last_dt >= due:
            return False
    return True


def mark_run(task_id: str, now: datetime) -> None:
    tasks = load_tasks()
    for t in tasks:
        if t.get("id") == task_id:
            t["last_run"] = now.isoformat(timespec="seconds")
            break
    save_tasks(tasks)


def due_tasks(now: datetime) -> list[dict[str, Any]]:
    """지금 실행할 예약 작업 목록 (last_run 즉시 갱신)."""
    tasks = load_tasks()
    due = [t for t in tasks if task_due(t, now)]
    for t in due:
        mark_run(t["id"], now)
    return due


def task_description(task: dict[str, Any]) -> str:
    spec = task.get("spec") or {}
    if spec.get("period") == "weekly":
        day = _WEEKDAY_NAMES.get(int(spec.get("weekday", 0)), "?")
        when = f"매주 {day}요일 {spec.get('hour', 0):02d}:{spec.get('minute', 0):02d}"
    else:
        when = f"매일 {spec.get('hour', 0):02d}:{spec.get('minute', 0):02d}"
    return f"{when} — {task.get('action', '')}"


def parse_command(text: str) -> tuple[str | None, dict[str, Any]]:
    """예약 명령 해석: ('add'|'list'|'remove', payload). 해당 없으면 (None, {})."""
    t = " ".join(str(text or "").split())
    spec, action = split_schedule_action(t)
    if spec:
        if not action:
            action = t
        return "add", {"spec": spec, "action": action}
    if any(k in t for k in ("예약 목록", "예약 보여", "스케줄 목록", "일정 목록")):
        return "list", {}
    m = re.search(r"(?:예약|스케줄|일정)\s*(?:취소|삭제|지워)\s*(?:해줘|줘)?\s*([0-9a-f]{6,8})?", t)
    if m and ("취소" in t or "삭제" in t or "지워" in t):
        return "remove", {"task_id": (m.group(1) or "").strip()}
    return None, {}
