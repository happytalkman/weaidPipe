"""주제 트렌드 분석 (D2).

턴 타임스탬프 기준으로 시간 버킷별 주제 등장 빈도와 추세를 계산한다.
순수 함수 — 합성 턴으로 결정적 테스트 가능.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from core.graph_store import _find_label


def bucket_key(ts: datetime, granularity: str = "hour") -> str:
    """시간 버킷 키."""
    if granularity == "day":
        return ts.strftime("%Y-%m-%d")
    return ts.strftime("%Y-%m-%dT%H")


def topic_buckets(
    store: dict[str, Any],
    granularity: str = "hour",
    top_n: int = 5,
) -> list[dict[str, Any]]:
    """버킷별 상위 주제 빈도.

    turns의 user 발화 텍스트에 주제 라벨이 포함되면 해당 버킷에 카운트.
    반환: [{"bucket": str, "topics": [{"label": str, "count": int}]}]
    """
    buckets: dict[str, dict[str, int]] = {}
    order: list[str] = []
    topic_labels = [str(t.get("label", "")) for t in store.get("topics", []) if t.get("label")]
    for turn in store.get("turns", []):
        if turn.get("speaker") != "user":
            continue
        text = str(turn.get("text", ""))
        if not text:
            continue
        try:
            ts = datetime.fromisoformat(str(turn.get("ts", "")))
        except (TypeError, ValueError):
            continue
        key = bucket_key(ts, granularity)
        if key not in buckets:
            buckets[key] = {}
            order.append(key)
        for label in topic_labels:
            if label and label in text:
                buckets[key][label] = buckets[key].get(label, 0) + 1
    result = []
    for key in order[-24:]:
        top = sorted(buckets[key].items(), key=lambda kv: kv[1], reverse=True)[:top_n]
        result.append({"bucket": key, "topics": [{"label": l, "count": c} for l, c in top]})
    return result


def topic_trend(
    store: dict[str, Any],
    topic: str,
    granularity: str = "hour",
) -> dict[str, Any]:
    """특정 주제의 추세 (상승/하강/유지)."""
    resolved = _find_label(store, topic) or topic
    buckets = topic_buckets(store, granularity, top_n=50)
    counts: list[int] = []
    for b in buckets:
        for t in b["topics"]:
            if t["label"] == resolved:
                counts.append(t["count"])
                break
        else:
            counts.append(0)
    if len(counts) < 2:
        return {"topic": resolved, "direction": "stable", "counts": counts}
    first_half = sum(counts[: len(counts) // 2])
    second_half = sum(counts[len(counts) // 2 :])
    if second_half > first_half:
        direction = "rising"
    elif second_half < first_half:
        direction = "falling"
    else:
        direction = "stable"
    return {"topic": resolved, "direction": direction, "counts": counts}


def extract_trend_command(text: str) -> str | None:
    """'OO 주제 트렌드' / '최근 화두' → 주제명(없으면 '') 추출."""
    t = str(text or "")
    if not re.search(r"트렌드|화두|추세", t):
        return None
    m = re.search(r"(.+?)(?:의|에\s*대한)?\s*(?:트렌드|추세)", t)
    if m:
        return m.group(1).strip()
    return ""
