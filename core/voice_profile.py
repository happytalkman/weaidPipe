"""음성 화자 프로필 (화자 분리).

사용자 발화의 음향 특징(피치/에너지)으로 화자를 식별한다:

  * extract_features  — RMS + 자기상관 기반 F0(피치) 추정 (순수 함수)
  * classify          — 프로필 중심과의 거리로 최근접 화자 판정
  * update_profile    — 프로필 통계 갱신 (누적 평균)
  * 프로필 저장소      — memory/voice_profiles.json

피치 추정은 자기상관(autocorrelation) 방식: 50~400Hz 범위의 래그 중
상관계수가 가장 높은 지연을 찾아 F0를 계산한다.
"""
from __future__ import annotations

import json
import math
import uuid
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
PROFILES_FILE = BASE_DIR / "memory" / "voice_profiles.json"

PITCH_MIN_HZ = 50.0
PITCH_MAX_HZ = 400.0
CORR_THRESHOLD = 0.30
CLASSIFY_DISTANCE = 0.22


def extract_features(samples: list[float], sample_rate: int) -> dict[str, float]:
    """발화 신호에서 (rms, pitch, zero_cross) 특징을 추출한다."""
    n = len(samples)
    if n < sample_rate // 10:  # 최소 0.1초
        return {"rms": 0.0, "pitch": 0.0, "zero_cross": 0.0}
    rms = math.sqrt(sum(s * s for s in samples) / n)
    energy = sum(s * s for s in samples) / max(1, n)

    # 피치: 자기상관 — 임계값 첫 교차 후 상관이 감소할 때까지 언덕 오르기로 정점 탐색
    min_lag = int(sample_rate / PITCH_MAX_HZ)
    max_lag = int(sample_rate / PITCH_MIN_HZ)
    best_lag = 0
    found = False
    for lag in range(min_lag, min(max_lag, n // 2)):
        corr = sum(samples[i] * samples[i + lag] for i in range(n - lag)) / (n - lag)
        if corr / energy > CORR_THRESHOLD:
            best_lag = lag
            best_corr = corr
            for lag2 in range(lag + 1, min(max_lag, n // 2)):
                c2 = sum(samples[i] * samples[i + lag2] for i in range(n - lag2)) / (n - lag2)
                if c2 > best_corr:
                    best_corr = c2
                    best_lag = lag2
                else:
                    break
            found = True
            break
    pitch = 0.0
    if found and best_lag > 0:
        pitch = sample_rate / best_lag

    zero_cross = 0.0
    for i in range(1, n):
        if (samples[i] >= 0) != (samples[i - 1] >= 0):
            zero_cross += 1.0
    zero_cross = zero_cross / max(1, n)

    return {"rms": rms, "pitch": pitch, "zero_cross": zero_cross}


def _distance(f: dict[str, float], p: dict[str, Any]) -> float:
    """특징 ↔ 프로필 중심 거리 (피치 우세, RMS 보조)."""
    stats = p.get("stats") or {}
    dp = 0.0
    if f.get("pitch", 0) > 0 and stats.get("pitch"):
        dp = abs(f["pitch"] - stats["pitch"]) / max(stats["pitch"], 1.0)
    else:
        dp = 1.0
    dr = abs(f.get("rms", 0) - stats.get("rms", 0)) / max(stats.get("rms", 0), 0.05)
    return 0.75 * dp + 0.25 * min(1.0, dr)


def classify(features: dict[str, float], profiles: list[dict[str, Any]]) -> str | None:
    """최근접 프로필 id 반환. 임계값 밖이면 None(미등록 화자)."""
    if features.get("pitch", 0) <= 0:
        return None
    best_id = None
    best_dist = CLASSIFY_DISTANCE
    for p in profiles:
        d = _distance(features, p)
        if d < best_dist:
            best_dist = d
            best_id = p.get("id")
    return best_id


def update_profile(profile: dict[str, Any], features: dict[str, float]) -> dict[str, Any]:
    """프로필 통계를 누적 갱신한다."""
    stats = profile.setdefault("stats", {"count": 0, "rms": 0.0, "pitch": 0.0, "zero_cross": 0.0})
    count = int(stats.get("count", 0))
    for key in ("rms", "zero_cross"):
        if features.get(key, 0) > 0:
            stats[key] = (stats.get(key, 0) * count + features[key]) / (count + 1)
    if features.get("pitch", 0) > 0:
        stats["pitch"] = (stats.get("pitch", 0) * count + features["pitch"]) / (count + 1)
    stats["count"] = count + 1
    profile["samples"] = int(profile.get("samples", 0)) + 1
    return profile


def new_profile(name: str = "", features: dict[str, float] | None = None) -> dict[str, Any]:
    profile: dict[str, Any] = {
        "id": uuid.uuid4().hex[:8],
        "name": (name or "").strip()[:40],
        "created": "",
        "samples": 0,
        "stats": {"count": 0, "rms": 0.0, "pitch": 0.0, "zero_cross": 0.0},
    }
    if features:
        update_profile(profile, features)
    return profile


def load_profiles() -> list[dict[str, Any]]:
    try:
        if PROFILES_FILE.exists():
            data = json.loads(PROFILES_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
    except Exception:
        pass
    return []


def save_profiles(profiles: list[dict[str, Any]]) -> Path:
    PROFILES_FILE.parent.mkdir(parents=True, exist_ok=True)
    PROFILES_FILE.write_text(json.dumps(profiles, ensure_ascii=False, indent=2), encoding="utf-8")
    return PROFILES_FILE


def name_profile(profiles: list[dict[str, Any]], profile_id: str, name: str) -> bool:
    for p in profiles:
        if p.get("id") == profile_id:
            p["name"] = (name or "").strip()[:40]
            return True
    return False
