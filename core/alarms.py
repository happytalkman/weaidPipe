"""알람/타이머.

"10분 후에 알려줘" / "오후 3시에 알람" / "타이머 5분" 등 시간 기반 알림을
저장하고, 시간이 되면 실행 대상으로 반환한다.

저장: memory/alarms.json (id, due_iso, label, created, fired)
"""
from __future__ import annotations

import json
import re
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

BASE_DIR = Path(__file__).resolve().parent.parent
ALARMS_FILE = BASE_DIR / "memory" / "alarms.json"

_DURATION_RE = re.compile(
    r"(\d{1,3})\s*(?:시간|시간후|h|hour|hours)?\s*(?:후에)?\s*(?:알려|타이머|알람)?"
    r"|(\d{1,3})\s*분\s*(?:후에)?\s*(?:알려|타이머|알람)?"
    r"|(\d{1,3})\s*초\s*(?:후에)?\s*(?:알려|타이머|알람)?"
)
_ALARM_TIME_RE = re.compile(r"(?:오전|오후|저녁|밤|아침)?\s*(\d{1,2})시(?:\s*(\d{1,2})분)?")


def parse_duration(text: str) -> int | None:
    """'10분', '1시간 30분', '90초' → 초. 없으면 None."""
    t = str(text or "")
    total = 0
    found = False
    for m in re.finditer(r"(\d{1,3})\s*시간", t):
        total += int(m.group(1)) * 3600
        found = True
    for m in re.finditer(r"(\d{1,3})\s*분", t):
        total += int(m.group(1)) * 60
        found = True
    for m in re.finditer(r"(\d{1,3})\s*초", t):
        total += int(m.group(1))
        found = True
    return total if found else None


def parse_alarm_time(text: str, now: datetime | None = None) -> datetime | None:
    """'오후 3시 30분' → 오늘의 해당 시각 (지났으면 내일). 없으면 None."""
    now = now or datetime.now()
    m = _ALARM_TIME_RE.search(str(text or ""))
    if not m:
        return None
    hour = int(m.group(1))
    minute = int(m.group(2) or 0)
    if ("오후" in text) or ("저녁" in text) or ("밤" in text):
        if hour < 12:
            hour += 12
    due = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if due <= now:
        due += timedelta(days=1)
    return due


def load_alarms() -> list[dict[str, Any]]:
    try:
        if ALARMS_FILE.exists():
            data = json.loads(ALARMS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
    except Exception:
        pass
    return []


def save_alarms(alarms: list[dict[str, Any]]) -> Path:
    ALARMS_FILE.parent.mkdir(parents=True, exist_ok=True)
    ALARMS_FILE.write_text(json.dumps(alarms, ensure_ascii=False, indent=2), encoding="utf-8")
    return ALARMS_FILE


def add_alarm(due: datetime, label: str = "알람") -> dict[str, Any]:
    alarm: dict[str, Any] = {
        "id": uuid.uuid4().hex[:8],
        "due": due.isoformat(timespec="seconds"),
        "label": (label or "알람").strip()[:120],
        "created": datetime.now().isoformat(timespec="seconds"),
        "fired": False,
    }
    alarms = load_alarms()
    alarms.append(alarm)
    save_alarms(alarms)
    return alarm


def remove_alarm(alarm_id: str) -> bool:
    alarms = load_alarms()
    remaining = [a for a in alarms if a.get("id") != alarm_id]
    if len(remaining) == len(alarms):
        return False
    save_alarms(remaining)
    return True


def due_alarms(now: datetime) -> list[dict[str, Any]]:
    """지금 울릴 알람 목록 (fired 표시)."""
    alarms = load_alarms()
    due = []
    changed = False
    for a in alarms:
        if a.get("fired"):
            continue
        try:
            due_dt = datetime.fromisoformat(a["due"])
        except (TypeError, ValueError):
            continue
        if now >= due_dt:
            a["fired"] = True
            changed = True
            due.append(a)
    if changed:
        save_alarms(alarms)
    return due


def alarm_description(alarm: dict[str, Any]) -> str:
    try:
        due_dt = datetime.fromisoformat(alarm["due"])
        when = due_dt.strftime("%m-%d %H:%M")
    except (TypeError, ValueError):
        when = alarm.get("due", "?")
    return f"[{alarm.get('id')}] {when} — {alarm.get('label', '')}"


class AlarmManager:
    """백그라운드 알람 루프 (interval 초 간격 체크)."""

    def __init__(self, on_fire: Callable[[dict[str, Any]], None], interval_seconds: int = 5):
        self.on_fire = on_fire
        self.interval = max(1, int(interval_seconds))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        if self._thread is not None and self._thread.is_alive():
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="alarms")
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                for alarm in due_alarms(datetime.now()):
                    cb = self.on_fire
                    if cb is not None:
                        try:
                            cb(alarm)
                        except Exception:
                            pass
            except Exception:
                pass
            for _ in range(self.interval):
                if self._stop.is_set():
                    return
                time.sleep(1)
