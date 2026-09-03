"""홀로그램 3D 아바타 수학 코어 (순수 함수 — 단위 테스트 가능).

HUD 중앙에 표시할 의사-3D 와이어프레임 '홀로그램 헤드'의 기하 계산:

  * 경위선(위도/경도) 구 격자 생성
  * 3D 회전(요/피치) → 정사영(orthographic) 2D 투영
  * 홀로그램 스캔라인/플리커 페이즈 계산
"""
from __future__ import annotations

import math
from typing import Any


def compute_grid(lat: int = 8, lon: int = 14, rot_y: float = 0.0, rot_x: float = 0.25) -> list[list[tuple[float, float, float]]]:
    """위도/경도 격자점의 3D 좌표 (단위 구, 회전 적용 후)."""
    rows: list[list[tuple[float, float, float]]] = []
    cos_rx, sin_rx = math.cos(rot_x), math.sin(rot_x)
    cos_ry, sin_ry = math.cos(rot_y), math.sin(rot_y)
    for i in range(lat + 1):
        phi = -math.pi / 2 + math.pi * i / max(1, lat)  # 위도
        row: list[tuple[float, float, float]] = []
        for j in range(lon + 1):
            theta = 2 * math.pi * j / max(1, lon)  # 경도
            x = math.cos(phi) * math.cos(theta)
            y = math.sin(phi)
            z = math.cos(phi) * math.sin(theta)
            # Y축 회전 후 X축 회전
            x2 = x * cos_ry - z * sin_ry
            z2 = x * sin_ry + z * cos_ry
            y2 = y * cos_rx - z2 * sin_rx
            z3 = y * sin_rx + z2 * cos_rx
            row.append((x2, y2, z3))
        rows.append(row)
    return rows


def project(points: list[tuple[float, float, float]], cx: float, cy: float, radius: float) -> list[tuple[float, float]]:
    """정사영 투영 (z는 무시, xy만 사용)."""
    return [(cx + x * radius, cy - y * radius) for x, y, _z in points]


def is_within_radius(
    points: list[tuple[float, float, float]], radius: float, tol: float = 1e-6
) -> bool:
    """모든 격자점이 단위 구 반경 이내인지 (수치 안정성 검증)."""
    for x, y, z in points:
        r = math.sqrt(x * x + y * y + z * z)
        if r > radius + tol or r < radius - tol:
            return False
    return True


def scanline_phase(tick: int, y_norm: float, speed: float = 0.02) -> float:
    """홀로그램 스캔라인 밝기 (0~1) — 위로 흐르는 밝은 띠."""
    phase = math.sin(tick * speed + y_norm * 6.0)
    return max(0.0, min(1.0, 0.35 + 0.65 * phase))


def flicker(tick: int) -> float:
    """홀로그램 특유의 미세 플리커 (0.85~1.0 보장)."""
    return 0.925 + 0.075 * math.sin(tick * 0.17) * math.cos(tick * 0.043)


def head_profile(t: float) -> tuple[float, float]:
    """머리 실루엣 윤곽 (파라메트릭, 0<=t<=1)."""
    ang = t * 2 * math.pi
    return math.cos(ang), 0.62 * math.sin(ang)
