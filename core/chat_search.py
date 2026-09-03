"""대화 로그 전문 검색 (B3).

과거 대화 턴을 쿼리로 검색한다 ('지난주에 OO 뭐라고 했지?').
순수 함수: tokenize, search_turns, extract_search_command.
"""
from __future__ import annotations

import re
from typing import Any


def tokenize(text: str) -> list[str]:
    return [t for t in re.split(r"[^\w가-힣]+", str(text or "").lower()) if len(t) >= 2]


def search_turns(store: dict[str, Any], query: str, top_k: int = 5) -> list[dict[str, Any]]:
    """턴 텍스트에서 쿼리 단어가 부분 포함되는 턴을 점수순 반환."""
    tokens = tokenize(query)
    if not tokens:
        return []
    scored: list[tuple[float, dict[str, Any]]] = []
    for turn in store.get("turns", []):
        text = str(turn.get("text", ""))
        lowered = text.lower()
        hits = sum(1 for t in tokens if t in lowered)
        if hits == 0:
            continue
        # 최신 턴에 가중치
        score = hits + (int(turn.get("seq", 0)) / 10000.0)
        scored.append((score, turn))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [t for _, t in scored[:top_k]]


def extract_search_command(text: str) -> str | None:
    """'지난주에 OO 뭐라고 했지' / '이전 대화에서 OO 찾아줘' → 쿼리."""
    t = str(text or "").strip()
    if not t:
        return None
    m = re.search(r"(?:이전|지난|예전|과거)?\s*(?:대화|말|내용)(?:에서|에서\s*내가)?\s*(.+?)(?:뭐라고|뭐라\s*했|찾아|검색)", t)
    if m:
        q = m.group(1).strip()
        if 1 <= len(q) <= 60:
            return q
    m = re.search(r"(.+?)\s*(?:뭐라고\s*했|말했|얘기했)(?:지|어|었지)?", t)
    if m:
        q = m.group(1).strip()
        # 시간 표현 접두사 제거
        q = re.sub(r"^(?:지난주에|이번주에|지난주|이번주|어제|오늘|예전에|과거에)\s*", "", q)
        if 1 <= len(q) <= 60:
            return q
    return None


def format_results(turns: list[dict[str, Any]]) -> str:
    if not turns:
        return "이전 대화에서 관련 내용을 찾지 못했습니다."
    lines = ["🔍 이전 대화 검색 결과:"]
    for t in turns:
        who = "사용자" if t.get("speaker") == "user" else "AID"
        lines.append(f"- [{who}] {str(t.get('text', ''))[:120]}")
    return "\n".join(lines)
