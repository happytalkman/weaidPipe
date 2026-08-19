"""오디오 자동 복구 (드라이버 장애 → 무음 → 자동 재시도).

순수 함수: RetryPolicy (지수 백오프 판정 — 테스트 가능)
통합: _play_audio 실패 시 RetryPolicy에 따라 주기적으로 스트림 재생성
"""
from __future__ import annotations

import time


class RetryPolicy:
    """지수 백오프 재시도 정책.

    attempt 1회 실패 → 10초 후 재시도, 이후 2배씩 (최대 5분).
    성공하면 초기화.
    """

    def __init__(self, base_seconds: float = 10.0, max_seconds: float = 300.0):
        self.base = float(base_seconds)
        self.max = float(max_seconds)
        self._attempts = 0
        self._last_fail: float | None = None

    def on_failure(self, now: float | None = None) -> None:
        self._attempts += 1
        self._last_fail = time.time() if now is None else float(now)

    def on_success(self) -> None:
        self._attempts = 0
        self._last_fail = None

    def next_delay(self) -> float:
        delay = min(self.max, self.base * (2 ** max(0, self._attempts - 1)))
        return delay

    def should_retry(self, now: float | None = None) -> bool:
        """마지막 실패 후 next_delay 만큼 지났으면 True."""
        now = time.time() if now is None else float(now)
        if self._attempts <= 0 or self._last_fail is None:
            return False
        return (now - self._last_fail) >= self.next_delay()


def audio_state_name(ok: bool, retrying: bool) -> str:
    """상태 문자열 (로그/UI용)."""
    if ok:
        return "audio_ok"
    if retrying:
        return "audio_retrying"
    return "audio_silent"
