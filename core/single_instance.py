"""단일 인스턴스 보장 (A1).

앱이 여러 개 떠서 마이크·오디오를 서로 점유하는 문제를 근본 차단한다.
PID 기반 잠금 파일로 두 번째 실행을 거부한다. 순수 로직은 테스트 가능.

  * acquire(app_id) -> bool   — 잠금 획득 (이미 살아 있으면 False)
  * release()                 — 잠금 해제
  * is_pid_alive(pid)         — 프로세스 생존 확인 (주입 가능)
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

BASE_DIR = Path(__file__).resolve().parent.parent
LOCK_FILE = BASE_DIR / "memory" / ".weaid.lock"


def is_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def acquire(app_id: str = "weaid", pid_alive: Callable[[int], bool] | None = None) -> bool:
    """단일 인스턴스 잠금 획득. 이미 실행 중이면 False."""
    alive = pid_alive or is_pid_alive
    try:
        if LOCK_FILE.exists():
            try:
                existing_pid = int(LOCK_FILE.read_text(encoding="utf-8").strip())
            except (ValueError, OSError):
                existing_pid = 0
            if existing_pid and alive(existing_pid):
                return False
        LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
        LOCK_FILE.write_text(str(os.getpid()), encoding="utf-8")
        return True
    except OSError:
        return False


def release() -> None:
    try:
        if LOCK_FILE.exists():
            LOCK_FILE.unlink()
    except OSError:
        pass


def close_behavior(settings: dict, tray_available: bool) -> str:
    """창 닫기 동작 결정: 'tray'(숨김) | 'quit'(종료)."""
    if not tray_available:
        return "quit"
    behavior = str(settings.get("close_behavior", "tray")).strip().lower()
    return "tray" if behavior == "tray" else "quit"
