"""다국어 (i18n) — 한/영 전환.

시스템 로그/알림 메시지의 한국어↔영어 번역 테이블.
settings.language("ko"|"en")로 전환한다. 순수 함수 (테스트 가능).
"""
from __future__ import annotations

from typing import Any

MESSAGES: dict[str, dict[str, str]] = {
    "wake_clap": {"ko": "박수 2번 감지 — AID 깨어났습니다.", "en": "Two claps detected — AID is awake."},
    "stop_clap": {"ko": "박수 1번 — 헌법 1조 멈춤.", "en": "One clap — Constitution Article 1: stop."},
    "alarm_fired": {"ko": "알람! — {label}", "en": "Alarm! — {label}"},
    "mindmap_opened": {"ko": "마인드맵 뷰어를 열었습니다.", "en": "Mindmap viewer opened."},
    "ontology_opened": {"ko": "온톨로지 뷰어를 열었습니다.", "en": "Ontology viewer opened."},
    "summary_done": {"ko": "대화 요약 노드 생성", "en": "Conversation summary node created"},
    "new_session": {"ko": "새 대화 세션 시작", "en": "New conversation session started"},
    "constitution_review": {"ko": "헌법 검사 {score}/100", "en": "Constitution review {score}/100"},
    "unknown": {"ko": "알 수 없는 오류", "en": "Unknown error"},
}


def t(key: str, lang: str = "ko", **kwargs: Any) -> str:
    """키 → 번역 문자열 (폴백: 키 또는 한국어)."""
    entry = MESSAGES.get(key)
    if not entry:
        return key
    text = entry.get(lang) or entry.get("ko", key)
    if kwargs:
        try:
            text = text.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            pass
    return text


def normalize_lang(lang: str) -> str:
    """'한국어'/'영어'/'ko'/'en' → 'ko'|'en'."""
    s = str(lang or "").strip().lower()
    if s in ("ko", "kr", "한국어", "한글", "korean"):
        return "ko"
    if s in ("en", "영어", "english"):
        return "en"
    return "ko"


def keys() -> list[str]:
    return sorted(MESSAGES.keys())


def completeness(lang: str) -> float:
    """해당 언어의 번역 커버리지 (0~1)."""
    lang = normalize_lang(lang)
    total = len(MESSAGES)
    filled = sum(1 for entry in MESSAGES.values() if entry.get(lang))
    return round(filled / total, 2) if total else 0.0
