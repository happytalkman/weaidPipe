"""대화 요약 노드 생성기 (그래프 자동 압축).

대화가 길어지면(기본 10턴마다) LLM(Gemini→Grok 폴백)으로 누적 대화를
요약해 그래프에 '요약 노드'로 추가한다:

  * summary     — 2~3문장 한국어 요약
  * key_topics  — 핵심 주제 (최대 5)
  * key_insights— 핵심 인사이트 (특히 인과 발견, 최대 3)

LLM 실패 시 로컬 폴백(주제/인과 요약)으로 항상 요약을 생성한다.
"""
from __future__ import annotations

from typing import Any

from core import graph_store

_SYSTEM = (
    "You are the WEAID conversation summarizer. Given the conversation turns "
    "and accumulated knowledge graph, produce a compact summary. Respond with "
    "JSON only, no markdown fences. Shape: "
    '{"summary":str,"key_topics":[str],"key_insights":[str]}. '
    "summary: 2-3 Korean sentences capturing what was discussed and decided. "
    "key_topics: up to 5 main subjects. key_insights: up to 3 findings, "
    "especially causal discoveries (X causes Y)."
)


def _turn_range(store: dict[str, Any]) -> tuple[int, int]:
    pairs = len(store.get("turns", [])) // 2
    last_end = 0
    for s in store.get("summaries", []):
        try:
            last_end = max(last_end, int(s.get("end_turn", 0)))
        except (TypeError, ValueError):
            pass
    return last_end + 1, pairs


def _local_fallback(store: dict[str, Any], start: int, end: int) -> dict[str, Any]:
    causal = [r for r in store.get("relations", []) if r.get("kind") == "causal"]
    return {
        "start_turn": start,
        "end_turn": end,
        "summary": (
            f"{start}~{end}턴 대화 요약: 주제 {len(store.get('topics', []))}개, "
            f"트리플 {len(store.get('triples', []))}개, 인과관계 {len(causal)}개가 축적되었습니다."
        ),
        "key_topics": [str(t["label"])[:60] for t in store.get("topics", [])][-5:],
        "key_insights": [f"{r['source']} → {r['target']}" for r in causal][-3:],
    }


def summarize_conversation(store: dict[str, Any], timeout_s: float = 45.0) -> dict[str, Any]:
    """이전 요약 이후 구간을 요약한다 (LLM 우선, 로컬 폴백)."""
    start, end = _turn_range(store)
    if end < start:
        start = max(1, end - 9)
    if end < 1:
        return _local_fallback(store, 0, 0)

    turns = store.get("turns", [])
    lines: list[str] = []
    for t in turns:
        pair = (int(t.get("seq", 0)) + 1) // 2
        if pair >= start:
            speaker = "질문" if t.get("speaker") == "user" else "답변"
            lines.append(f"{speaker}{pair}: {str(t.get('text', ''))[:180]}")
    context = "\n".join(lines[-40:])
    topics = ", ".join(str(t["label"]) for t in store.get("topics", [])[-15:])
    triples = store.get("triples", [])[-30:]
    triples_text = "; ".join(
        f"{x['subject']}→{x['predicate']}→{x['object']}" for x in triples
    )
    causal = [r for r in store.get("relations", []) if r.get("kind") == "causal"][-10:]
    causal_text = "; ".join(f"{r['source']}→{r['target']}" for r in causal)

    prompt = (
        f"대화 턴 ({start}~{end}):\n{context}\n\n"
        f"누적 주제: {topics}\n\n"
        f"S-P-O 트리플:\n{triples_text}\n\n"
        f"인과관계:\n{causal_text}"
    )
    try:
        from core.llm import generate_json

        data = generate_json(_SYSTEM, prompt, timeout_s=timeout_s)
        return {
            "start_turn": start,
            "end_turn": end,
            "summary": str(data.get("summary", ""))[:800],
            "key_topics": [str(x)[:60] for x in (data.get("key_topics") or [])][:5],
            "key_insights": [str(x)[:160] for x in (data.get("key_insights") or [])][:3],
        }
    except Exception:
        return _local_fallback(store, start, end)
