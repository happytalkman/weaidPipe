"""크래시 부검 (A2).

앱이 예기치 않게 죽을 때 원인을 자동 기록한다:

  * enable() — faulthandler + 예외 훅 + 종료 훅 설치
  * crash 로그: logs/crash.log (마지막 2000자 유지)
  * 순수 함수 format_crash(ts, kind, detail) — 테스트 가능
"""
from __future__ import annotations

import faulthandler
import sys
import traceback
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
CRASH_LOG = BASE_DIR / "logs" / "crash.log"
MAX_CRASH_BYTES = 200_000


def format_crash(ts: str, kind: str, detail: str) -> str:
    return f"[{ts}] [{kind}] {detail.strip()}\n"


def append_crash(kind: str, detail: str) -> Path:
    CRASH_LOG.parent.mkdir(parents=True, exist_ok=True)
    line = format_crash(datetime.now().isoformat(timespec="seconds"), kind, detail)
    with open(CRASH_LOG, "a", encoding="utf-8") as f:
        f.write(line)
    # 크기 제한 (뒤에서 잘라내기)
    if CRASH_LOG.stat().st_size > MAX_CRASH_BYTES:
        content = CRASH_LOG.read_text(encoding="utf-8", errors="replace")
        CRASH_LOG.write_text(content[-MAX_CRASH_BYTES // 2 :], encoding="utf-8")
    return CRASH_LOG


def _excepthook(exc_type, exc_value, exc_tb):
    append_crash("unhandled", "".join(traceback.format_exception(exc_type, exc_value, exc_tb)))
    sys.__excepthook__(exc_type, exc_value, exc_tb)


def enable() -> None:
    """크래시 부검 활성화 (앱 시작 시 1회 호출)."""
    try:
        CRASH_LOG.parent.mkdir(parents=True, exist_ok=True)
        faulthandler.enable(open(CRASH_LOG, "a", encoding="utf-8"))
    except OSError:
        pass
    sys.excepthook = _excepthook


def last_crash(limit_lines: int = 10) -> str:
    """최근 크래시 로그 반환 (자가진단용)."""
    try:
        if not CRASH_LOG.exists():
            return ""
        lines = CRASH_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-limit_lines:])
    except OSError:
        return ""
