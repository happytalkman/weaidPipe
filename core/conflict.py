"""지식 충돌 감지 (C1).

누적 트리플에서 같은 주어+서술어에 서로 다른 목적어가 쌓이거나,
부정 표현(아니다/없다)이 긍정 표현과 충돌하면 경고한다.

순수 함수: detect_conflicts(triples)
"""
from __future__ import annotations

import re
from typing import Any

_NEGATION_WORDS = ("아니다", "아닙니다", "없다", "없습니다", "불가능", "불가", "금지", "못한다")


def _is_negative(text: str) -> bool:
    return any(w in str(text) for w in _NEGATION_WORDS)


def detect_conflicts(triples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """충돌 트리플 쌍 탐지.

    유형:
      * same_spo: 동일 (주어, 서술어)에 서로 다른 목적어 2개 이상
      * negation: 한쪽은 긍정, 다른 쪽은 부정(같은 주어+서술어)
    """
    conflicts: list[dict[str, Any]] = []
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for t in triples:
        if not isinstance(t, dict):
            continue
        key = (str(t.get("subject", "")).strip(), str(t.get("predicate", "")).strip())
        if key[0] and key[1]:
            groups.setdefault(key, []).append(t)

    seen_pairs: set[tuple[int, int]] = set()
    for (s, p), items in groups.items():
        objects = [str(x.get("object", "")).strip() for x in items if str(x.get("object", "")).strip()]
        distinct = list(dict.fromkeys(objects))
        if len(distinct) >= 2:
            neg = any(_is_negative(o) for o in distinct)
            conflicts.append({
                "type": "negation" if neg else "same_spo",
                "subject": s,
                "predicate": p,
                "objects": distinct,
            })
    return conflicts


def conflict_report(conflicts: list[dict[str, Any]]) -> str:
    if not conflicts:
        return ""
    lines = [f"⚠️ 지식 충돌 {len(conflicts)}건 감지:"]
    for c in conflicts[:5]:
        kind = "부정 충돌" if c["type"] == "negation" else "중복 정의"
        lines.append(f"- [{kind}] {c['subject']} {c['predicate']} → {' vs '.join(c['objects'])}")
    return "\n".join(lines)
