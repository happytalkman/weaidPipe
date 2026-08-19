"""장기 기억 강화 (B1).

대화에서 사용자 선호/사실을 추출해 장기 기억으로 저장하고, 질의 시
키워드 기반으로 회상한다. 순수 함수 위주 (테스트 가능).

저장: memory/long_term_prefs.json
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
MEMORY_FILE = BASE_DIR / "memory" / "long_term_prefs.json"

_PREF_PATTERNS = (
    re.compile(r"(?:나는|저는|제가)\s*(.+?)(?:을|를)?\s*(?:좋아해|선호해|좋아합니다|선호합니다)"),
    re.compile(r"(?:나는|저는)\s*(.+?)(?:이야|입니다|이에요)"),
    re.compile(r"(?:기억해줘|기억해|메모해줘)\s*[:：]?\s*(.+)"),
)


def extract_memory_candidates(question: str, answer: str) -> list[str]:
    """Q&A에서 기억 후보 문장을 추출한다 (결정적 휴리스틱)."""
    candidates: list[str] = []
    for text in (question, answer):
        for pat in _PREF_PATTERNS:
            m = pat.search(str(text or ""))
            if m:
                c = m.group(1).strip()[:200]
                if c and c not in candidates:
                    candidates.append(c)
    return candidates[:5]


def add_memory(
    entries: list[dict[str, Any]],
    text: str,
    category: str = "preference",
) -> list[dict[str, Any]]:
    """메모리 추가 (중복 시 카운트 증가, 최신순 유지, 상한 캡)."""
    text = str(text or "").strip()
    if not text:
        return entries
    for e in entries:
        if e.get("text") == text:
            e["count"] = int(e.get("count", 1)) + 1
            e["updated"] = datetime.now().isoformat(timespec="seconds")
            return entries
    entries.insert(0, {
        "text": text,
        "category": category,
        "count": 1,
        "created": datetime.now().isoformat(timespec="seconds"),
        "updated": datetime.now().isoformat(timespec="seconds"),
    })
    return entries[:100]


def recall(entries: list[dict[str, Any]], query: str, limit: int = 5) -> list[dict[str, Any]]:
    """키워드 기반 회상: 질의 단어와 겹치는 메모리를 점수순 반환."""
    query = str(query or "")
    if not query:
        return []
    tokens = [t for t in re.split(r"[^\w가-힣]+", query.lower()) if len(t) >= 2]
    scored: list[tuple[int, dict[str, Any]]] = []
    for e in entries:
        text = str(e.get("text", "")).lower()
        score = sum(1 for t in tokens if t in text) + int(e.get("count", 1)) * 0.1
        if score > 0:
            scored.append((score, e))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [e for _, e in scored[:limit]]


def load_memory() -> list[dict[str, Any]]:
    try:
        if MEMORY_FILE.exists():
            data = json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
    except Exception:
        pass
    return []


def save_memory(entries: list[dict[str, Any]]) -> Path:
    MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    MEMORY_FILE.write_text(json.dumps(entries, ensure_ascii=False, indent=1), encoding="utf-8")
    return MEMORY_FILE


def memory_context(entries: list[dict[str, Any]], limit: int = 10) -> str:
    """시스템 프롬프트에 주입할 기억 컨텍스트."""
    if not entries:
        return ""
    lines = ["## 사용자 장기 기억"]
    for e in entries[:limit]:
        lines.append(f"- {e.get('text')}")
    return "\n".join(lines)
