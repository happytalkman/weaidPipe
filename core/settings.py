"""사용자 설정 저장소 (설정 UI 패널용).

.env 수동 편집 대신 앱 내 설정 패널에서 수정할 수 있는 타이핑된
설정 저장소. 순수 함수 위주 (테스트 가능).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
SETTINGS_FILE = BASE_DIR / "memory" / "user_settings.json"

DEFAULTS: dict[str, Any] = {
    "voice_name": "puck",
    "live_model": "",
    "graphics_quality": "medium",
    "proactive_interval": 30,
    "alarm_interval": 5,
    "summary_every_turns": 10,
    "hologram_on": False,
    "notifications_on": True,
    "tone_aware": True,
    "language": "ko",
}

_BOOL_KEYS = {"hologram_on", "notifications_on", "tone_aware"}
_INT_KEYS = {"proactive_interval", "alarm_interval", "summary_every_turns"}
_STR_KEYS = {"voice_name", "live_model", "graphics_quality", "language"}


def load_settings() -> dict[str, Any]:
    settings = dict(DEFAULTS)
    try:
        if SETTINGS_FILE.exists():
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                settings.update(data)
    except Exception:
        pass
    return settings


def save_settings(settings: dict[str, Any]) -> Path:
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    return SETTINGS_FILE


def get(key: str) -> Any:
    settings = load_settings()
    return settings.get(key, DEFAULTS.get(key))


def set_setting(key: str, value: Any) -> dict[str, Any]:
    """타입 검증 후 설정 저장. 검증 실패 시 현재값 유지."""
    settings = load_settings()
    if key not in DEFAULTS:
        return settings
    try:
        if key in _BOOL_KEYS:
            if isinstance(value, str):
                value = value.strip().lower() in {"1", "true", "yes", "on"}
            else:
                value = bool(value)
        elif key in _INT_KEYS:
            value = int(value)
            if value < 1:
                value = int(DEFAULTS[key])
        elif key in _STR_KEYS:
            value = str(value).strip()
            if not value:
                value = str(DEFAULTS[key])
        settings[key] = value
    except (TypeError, ValueError):
        return settings
    save_settings(settings)
    return settings


def as_env_updates() -> dict[str, str]:
    """설정 → .env에 반영할 키-값 쌍."""
    updates: dict[str, str] = {}
    s = load_settings()
    if s.get("voice_name"):
        updates["GEMINI_VOICE_NAME"] = str(s["voice_name"])
    if s.get("live_model"):
        updates["GEMINI_LIVE_MODEL"] = str(s["live_model"])
    return updates
