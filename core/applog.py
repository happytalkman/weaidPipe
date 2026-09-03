"""구조화 로깅 (일별 회전 + 오류 수집).

print 기반 산재 로그를 대체하는 가벼운 파일 로거:
  * logs/app-YYYYMMDD.log — 일별 회전
  * 오류 수집기 — 자가진단 대시보드에서 읽는 최근 오류 목록

순수 함수: log_path_for(date), prune_old_logs, ErrorCollector (테스트 가능)
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = BASE_DIR / "logs"
ERRORS_FILE = BASE_DIR / "logs" / "recent_errors.json"
KEEP_DAYS = 14
MAX_ERRORS = 100


def log_path_for(day: datetime | None = None) -> Path:
    day = day or datetime.now()
    return LOG_DIR / f"app-{day.strftime('%Y%m%d')}.log"


def log_write(message: str, level: str = "INFO") -> Path:
    """일별 로그 파일에 한 줄 추가."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = log_path_for()
    ts = datetime.now().strftime("%H:%M:%S")
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"[{ts}] [{level}] {message}\n")
    return path


def prune_old_logs(now: datetime | None = None, keep_days: int = KEEP_DAYS) -> int:
    """keep_days 이전 로그 파일 삭제. 삭제 개수 반환."""
    now = now or datetime.now()
    if not LOG_DIR.exists():
        return 0
    cutoff = now - timedelta(days=keep_days)
    removed = 0
    for f in LOG_DIR.glob("app-*.log"):
        try:
            day = datetime.strptime(f.stem, "app-%Y%m%d")
        except ValueError:
            continue
        if day < cutoff:
            try:
                f.unlink()
                removed += 1
            except OSError:
                pass
    return removed


class ErrorCollector:
    """최근 오류 수집 (자가진단 대시보드용)."""

    @staticmethod
    def record(message: str, source: str = "") -> None:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        errors: list[dict[str, Any]] = ErrorCollector.load()
        errors.append({
            "ts": datetime.now().isoformat(timespec="seconds"),
            "source": (source or "")[:80],
            "message": str(message)[:400],
        })
        errors = errors[-MAX_ERRORS:]
        ERRORS_FILE.write_text(json.dumps(errors, ensure_ascii=False, indent=1), encoding="utf-8")

    @staticmethod
    def load(limit: int = 50) -> list[dict[str, Any]]:
        try:
            if ERRORS_FILE.exists():
                data = json.loads(ERRORS_FILE.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    return data[-limit:]
        except Exception:
            pass
        return []

    @staticmethod
    def clear() -> None:
        try:
            if ERRORS_FILE.exists():
                ERRORS_FILE.unlink()
        except OSError:
            pass
