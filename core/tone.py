"""음성 톤 인식 (B4).

화자 프로필의 음향 특징(피치/에너지/영교차율)으로 톤을 추정한다.
순수 함수 — 합성 사인파로 결정적 테스트 가능.
"""
from __future__ import annotations

from typing import Any

TONE_LABELS = ("calm", "neutral", "excited", "urgent")


def classify_tone(features: dict[str, float]) -> str:
    """특징 → 톤 분류.

    urgent:  고에너지 + 높은 피치 + 높은 영교차율
    excited: 높은 에너지 또는 높은 피치
    calm:    낮은 에너지 + 낮은 영교차율
    neutral: 그 외
    """
    rms = float(features.get("rms", 0.0))
    pitch = float(features.get("pitch", 0.0))
    zc = float(features.get("zero_cross", 0.0))

    if rms >= 0.3 and pitch >= 180 and zc >= 0.04:
        return "urgent"
    if rms >= 0.2 or pitch >= 220:
        return "excited"
    if rms < 0.08 and zc < 0.03:
        return "calm"
    return "neutral"


def tone_guidance(tone: str) -> str:
    """톤에 따른 응답 지침 (시스템 프롬프트 부가용)."""
    return {
        "urgent": "사용자의 목소리가 긴박합니다. 짧고 즉시 실행 가능한 답을 최우선으로 하세요.",
        "excited": "사용자가 활기찹니다. 에너지를 맞추되 핵심은 간결하게.",
        "calm": "차분한 대화 분위기입니다. 여유 있게 친절하게 답하세요.",
        "neutral": "",
    }.get(tone, "")


def tone_history_average(history: list[str]) -> str:
    """최근 톤 다수결."""
    if not history:
        return "neutral"
    return max(set(history), key=history.count)
