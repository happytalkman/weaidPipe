"""Cumulative conversation graph store (누적 대화 지식그래프 저장소).

The whole conversation accumulates here turn by turn — this is the foundation
of the Palantir-style ontology: one persistent, growing graph of

  * turns     (every user/assistant utterance)
  * topics    (subjects discovered across the conversation)
  * triples   (S-P-O with semantic/kinetic/dynamic layers)
  * entities  (deduplicated by label, with occurrence counts)
  * relations (label-based, causal/attribute/... links)

The viewer renders THIS store, so the mindmap shows the conversation context
and the ontology shows the accumulated insight.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
GRAPH_PATH = BASE_DIR / "memory" / "conversation_graph.json"
SHARED_PATH = Path(__import__("tempfile").gettempdir()) / "weaid_mindmap_data.json"

_LAYERS = ("semantic", "kinetic", "dynamic")


def user_graph_path(user_id: str) -> Path:
    """Return an isolated graph path for a hosted user."""
    safe_id = re.sub(r"[^a-zA-Z0-9_-]", "", str(user_id))[:80]
    return BASE_DIR / "memory" / "users" / f"{safe_id}.json"


def load_for_user(user_id: str) -> dict[str, Any]:
    path = user_graph_path(user_id)
    try:
        if path.exists():
            store = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(store, dict) and "turns" in store:
                return store
    except Exception:
        pass
    return new_store()


def save_for_user(store: dict[str, Any], user_id: str) -> Path:
    store["updated"] = datetime.now().isoformat(timespec="seconds")
    path = user_graph_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(store, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def new_store() -> dict[str, Any]:
    now = datetime.now().isoformat(timespec="seconds")
    return {
        "version": 1,
        "created": now,
        "updated": now,
        "turns": [],
        "topics": [],
        "triples": [],
        "entities": [],
        "relations": [],
        "summaries": [],
    }


def load() -> dict[str, Any]:
    try:
        if GRAPH_PATH.exists():
            store = json.loads(GRAPH_PATH.read_text(encoding="utf-8"))
            if isinstance(store, dict) and "turns" in store:
                return store
    except Exception:
        pass
    return new_store()


def save(store: dict[str, Any]) -> Path:
    store["updated"] = datetime.now().isoformat(timespec="seconds")
    GRAPH_PATH.parent.mkdir(parents=True, exist_ok=True)
    GRAPH_PATH.write_text(json.dumps(store, ensure_ascii=False, indent=1), encoding="utf-8")
    return GRAPH_PATH


def publish(store: dict[str, Any]) -> Path:
    """Write the store to the temp file the viewer process watches."""
    SHARED_PATH.write_text(json.dumps(store, ensure_ascii=False), encoding="utf-8")
    return SHARED_PATH


def _norm_layer(layer: str) -> str:
    layer = str(layer or "semantic").strip().lower()
    return layer if layer in _LAYERS else "semantic"


def merge_turn(store: dict[str, Any], question: str, answer: str, *, heur_triples: list | None = None) -> dict[str, Any]:
    """Fast phase: append the raw turn (+ heuristic triples) immediately."""
    seq = len(store["turns"]) + 1
    now = datetime.now().isoformat(timespec="seconds")
    store["turns"].append({"seq": seq, "speaker": "user", "text": (question or "").strip()[:2000], "ts": now})
    store["turns"].append({"seq": seq + 1, "speaker": "assistant", "text": (answer or "").strip()[:4000], "ts": now})

    if heur_triples:
        _merge_insight_parts(store, heur_triples, [], [], [])
    return store


def merge_insight(store: dict[str, Any], insight: dict[str, Any]) -> dict[str, Any]:
    """Deep phase: merge the LLM insight (topics/triples/entities/relations)."""
    _merge_insight_parts(
        store,
        insight.get("triples") or [],
        insight.get("entities") or [],
        insight.get("relations") or [],
        insight.get("topics") or [],
    )
    return store


def _merge_insight_parts(
    store: dict[str, Any],
    triples: list,
    entities: list,
    relations: list,
    topics: list,
) -> None:
    # 이전 하이라이트 해제: 최신 턴에서 추가된 항목만 반짝이도록 한다.
    for coll in ("entities", "relations", "triples", "topics"):
        for item in store.get(coll, []):
            item.pop("new", None)

    # topics (by label, keep first layer, count occurrences, merge objects)
    topic_index = {t["label"]: t for t in store["topics"]}
    for tp in topics:
        if not isinstance(tp, dict) or not tp.get("label"):
            continue
        label = str(tp["label"]).strip()
        if label in topic_index:
            topic_index[label]["count"] = topic_index[label].get("count", 1) + 1
            for obj in tp.get("objects") or []:
                if obj not in topic_index[label].setdefault("objects", []):
                    topic_index[label]["objects"].append(obj)
        else:
            entry = dict(tp)
            entry["count"] = 1
            entry["new"] = True
            entry.setdefault("layer", _norm_layer(tp.get("layer", "semantic")))
            topic_index[label] = entry
            store["topics"].append(entry)

    # triples (dedupe by s+p+o)
    seen_triples = {(t["subject"], t["predicate"], t["object"]) for t in store["triples"]}
    for t in triples:
        if not isinstance(t, dict):
            continue
        s = str(t.get("subject", "")).strip()
        p = str(t.get("predicate", "")).strip()
        o = str(t.get("object", "")).strip()
        if not (s and p and o) or (s, p, o) in seen_triples:
            continue
        seen_triples.add((s, p, o))
        store["triples"].append({"subject": s, "predicate": p, "object": o, "layer": _norm_layer(t.get("layer")), "new": True})

    # entities (dedupe by label; ids in per-call payloads are unstable, so we
    # re-key the store by label and map relation endpoints below)
    entity_by_label: dict[str, dict[str, Any]] = {e["label"]: e for e in store["entities"]}
    label_to_canon: dict[str, str] = {}
    for e in store["entities"]:
        label_to_canon[e["label"]] = e["id"]
    for e in entities:
        if not isinstance(e, dict) or not e.get("label"):
            continue
        label = str(e["label"]).strip()
        kind = str(e.get("kind", "concept")).strip().lower()
        layer = _norm_layer(e.get("layer"))
        if label in entity_by_label:
            entity_by_label[label]["count"] = entity_by_label[label].get("count", 1) + 1
        else:
            eid = f"e{len(store['entities'])}"
            store["entities"].append({"id": eid, "label": label, "kind": kind, "layer": layer, "count": 1, "new": True})
            entity_by_label[label] = store["entities"][-1]
            label_to_canon[label] = eid

    # relations: convert endpoint ids → labels via per-call entity list
    call_id_label = {str(e.get("id")): str(e.get("label", "")).strip() for e in entities if isinstance(e, dict)}
    seen_rels = {(r["source"], r["target"], r["label"]) for r in store["relations"]}
    for r in relations:
        if not isinstance(r, dict):
            continue
        src_label = call_id_label.get(str(r.get("source", "")), str(r.get("source", "")).strip())
        tgt_label = call_id_label.get(str(r.get("target", "")), str(r.get("target", "")).strip())
        if not src_label or not tgt_label:
            continue
        if src_label in entity_by_label:
            entity_by_label[src_label].setdefault("kind", "concept")
        if tgt_label in entity_by_label:
            entity_by_label[tgt_label].setdefault("kind", "concept")
        label = str(r.get("label", "relates")).strip() or "relates"
        kind = str(r.get("kind", "associative")).strip().lower()
        if (src_label, tgt_label, label) in seen_rels:
            continue
        seen_rels.add((src_label, tgt_label, label))
        store["relations"].append({
            "source": src_label, "target": tgt_label,
            "label": label, "kind": kind, "layer": _norm_layer(r.get("layer")),
            "new": True,
        })

    # cap sizes so the graph stays renderable
    store["topics"] = store["topics"][-60:]
    store["triples"] = store["triples"][-300:]
    store["entities"] = store["entities"][-200:]
    store["relations"] = store["relations"][-400:]


def needs_summary(store: dict[str, Any], every_turns: int = 10) -> bool:
    """마지막 요약 이후 every_turns 턴이 지났으면 True."""
    pairs = len(store.get("turns", [])) // 2
    if pairs < every_turns:
        return False
    last_end = 0
    for s in store.get("summaries", []):
        try:
            last_end = max(last_end, int(s.get("end_turn", 0)))
        except (TypeError, ValueError):
            pass
    return (pairs - last_end) >= every_turns


def merge_summary(store: dict[str, Any], summary: dict[str, Any]) -> dict[str, Any]:
    """요약 노드를 누적 그래프에 추가한다 (최대 20개 유지)."""
    store.setdefault("summaries", []).append(summary)
    store["summaries"] = store["summaries"][-20:]
    return store


def stats(store: dict[str, Any]) -> str:
    return (
        f"턴 {len(store['turns'])} · 주제 {len(store['topics'])} · 트리플 {len(store['triples'])} · "
        f"엔티티 {len(store['entities'])} · 관계 {len(store['relations'])} · 요약 {len(store.get('summaries', []))}"
    )


def reset(archive: bool = True) -> dict[str, Any]:
    """새 대화 세션 시작: 기존 그래프를 sessions/ 폴더에 아카이브하고 새 그래프 반환."""
    try:
        if archive and GRAPH_PATH.exists():
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            arch = GRAPH_PATH.parent / "sessions" / f"graph-{stamp}.json"
            arch.parent.mkdir(parents=True, exist_ok=True)
            GRAPH_PATH.replace(arch)
    except Exception:
        pass
    store = new_store()
    save(store)
    return store


def list_sessions() -> list[Path]:
    """아카이브된 이전 세션 목록 (최신순)."""
    sessions_dir = GRAPH_PATH.parent / "sessions"
    if not sessions_dir.exists():
        return []
    return sorted(sessions_dir.glob("graph-*.json"), reverse=True)


def _find_label(store: dict[str, Any], term: str) -> str | None:
    """정확/부분/단어 단위 일치로 엔티티·주제 라벨을 찾는다."""
    term = str(term or "").strip()
    if not term:
        return None
    labels = [e["label"] for e in store.get("entities", [])]
    labels += [t["label"] for t in store.get("topics", [])]
    for l in labels:
        if l == term:
            return l
    for l in labels:
        if term in l or l in term:
            return l
    # 단어 단위 매칭 (조사가 붙은 term 대응)
    for l in labels:
        for w in term.split():
            if len(w) >= 2 and (w in l or l in w):
                return l
    return None


def trace_causal(store: dict[str, Any], target: str, max_depth: int = 5) -> list[dict]:
    """대상의 원인 체인을 인과 관계를 따라 역추적한다.

    반환: [{"cause":..., "effect":..., "label":...}, ...] (원인→결과 순)
    """
    target_label = _find_label(store, target)
    if not target_label:
        return []
    causal = [r for r in store.get("relations", []) if r.get("kind") == "causal"]
    chain: list[dict] = []
    current = target_label
    for _ in range(max(1, int(max_depth))):
        causes = [r for r in causal if r["target"] == current]
        if not causes:
            break
        r = causes[0]
        chain.append({"cause": r["source"], "effect": current, "label": r.get("label", "유발")})
        current = r["source"]
    chain.reverse()
    return chain


def causal_focus(store: dict[str, Any], target: str) -> dict[str, Any] | None:
    """인과 체인의 뷰어 포커스 페이로드 (경로 하이라이트용)."""
    chain = trace_causal(store, target)
    if not chain:
        return None
    labels: list[str] = []
    for step in chain:
        for lbl in (step["cause"], step["effect"]):
            if lbl not in labels:
                labels.append(lbl)
    entities = [e for e in store.get("entities", []) if e["label"] in labels]
    relations = [
        r for r in store.get("relations", [])
        if r["source"] in labels and r["target"] in labels and r.get("kind") == "causal"
    ]
    return {"term": target, "matched": labels, "entities": entities, "relations": relations, "chain": chain}


def chain_to_korean(chain: list[dict]) -> str:
    """인과 체인을 한국어 설명으로 변환."""
    if not chain:
        return ""
    flow = " → ".join([chain[0]["cause"]] + [s["effect"] for s in chain])
    steps = []
    for s in chain:
        steps.append(f"{s['cause']}이(가) {s['effect']}을(를) 일으켰고")
    body = " ".join(steps)
    body = body.rstrip("그리고").rstrip("고") + "습니다"
    return f"원인 체인: {flow}. {body}."


def query(store: dict[str, Any], terms: list[str]) -> dict[str, Any]:
    """질의 기반 탐색: 검색어와 일치하는 노드 + 1홉 이웃의 서브그래프 반환."""
    terms = [str(t).strip() for t in (terms or []) if str(t).strip()]
    if not terms:
        return {"term": "", "matched": [], "entities": [], "relations": []}

    matched: list[str] = []
    for e in store.get("entities", []):
        if any(term in e["label"] for term in terms) and e["label"] not in matched:
            matched.append(e["label"])
    for t in store.get("topics", []):
        if any(term in t["label"] for term in terms) and t["label"] not in matched:
            matched.append(t["label"])
    for tr in store.get("triples", []):
        if any(term in tr["subject"] or term in tr["object"] for term in terms):
            for lbl in (tr["subject"], tr["object"]):
                if lbl and lbl not in matched:
                    matched.append(lbl)
    matched = matched[:20]
    if not matched:
        return {"term": terms[0], "matched": [], "entities": [], "relations": []}

    matched_set = set(matched)
    sub_rels = [
        r for r in store.get("relations", [])
        if r["source"] in matched_set or r["target"] in matched_set
    ][:120]
    related: set[str] = set()
    for r in sub_rels:
        related.add(r["source"])
        related.add(r["target"])

    sub_entities = [e for e in store.get("entities", []) if e["label"] in related][:80]
    # 서브그래프에 없는 라벨은 합성 엔티티로 추가
    have = {e["label"] for e in sub_entities}
    for lbl in related - have:
        layer = "semantic"
        for r in sub_rels:
            if r["source"] == lbl or r["target"] == lbl:
                layer = r.get("layer", "semantic")
                break
        sub_entities.append({"id": f"q{len(sub_entities)}", "label": lbl, "kind": "concept", "layer": layer, "count": 1})

    return {
        "term": terms[0],
        "matched": sorted(matched),
        "entities": sub_entities,
        "relations": sub_rels,
    }
