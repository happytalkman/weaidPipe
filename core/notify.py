"""알림 통합 (Windows 토스트) — 스팸 방지 포함.

순수 함수: sanitize_message, RateLimiter (테스트 가능)
플랫폼별 전송: notify() (Windows PowerShell 토스트, 실패 시 무시)
"""
from __future__ import annotations

import subprocess
import time
from typing import Any


def sanitize_message(text: str, max_len: int = 200) -> str:
    """토스트에 안전한 메시지로 정제."""
    text = str(text or "").strip()
    if not text:
        return ""
    text = text.replace("'", "''").replace('"', "'")
    return text[:max_len]


class RateLimiter:
    """같은 종류의 알림을 cooldown 동안 반복하지 않는다."""

    def __init__(self, cooldown_seconds: float = 60.0):
        self.cooldown = float(cooldown_seconds)
        self._last: dict[str, float | None] = {}

    def allow(self, key: str, now: float | None = None) -> bool:
        now = time.time() if now is None else float(now)
        last = self._last.get(key)
        if last is None or now - last >= self.cooldown:
            self._last[key] = now
            return True
        return False

    def reset(self, key: str) -> None:
        self._last.pop(key, None)


def notify(title: str, message: str, timeout_ms: int = 5000) -> bool:
    """Windows 토스트 알림 전송. 실패해도 False만 반환 (무해)."""
    t = sanitize_message(title, 80)
    m = sanitize_message(message)
    if not t:
        return False
    try:
        script = (
            "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null; "
            "[Windows.UI.Notifications.ToastNotification, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null; "
            "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType = WindowsRuntime] | Out-Null; "
            "$t='%s'; $m='%s'; "
            "$xml = New-Object Windows.Data.Xml.Dom.XmlDocument; "
            "$xml.LoadXml(\"<toast><visual><binding template='ToastGeneric'><text>$t</text><text>$m</text></binding></visual></toast>\"); "
            "$toast = New-Object Windows.UI.Notifications.ToastNotification $xml; "
            "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('WEAID').Show($toast)"
            % (t, m)
        )
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True, timeout=max(10, timeout_ms // 1000 + 3),
        )
        return True
    except Exception:
        return False
