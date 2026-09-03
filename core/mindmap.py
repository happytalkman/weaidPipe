"""Mindmap + knowledge-graph extraction for WEAID.

Splits an answer into Subject-Predicate-Object (S-P-O) triples and organises
them into three layers:

  * Semantic (S): what something IS   (definitions, attributes, classifications)
  * Kinetic  (K): what something DOES (actions, processes, transitions)
  * Dynamic  (D): how things CHANGE   (causes, effects, temporal evolution)

The result is rendered by D3.js (``web/mindmap.html``) as a mindmap on one side
and a causal knowledge graph on the other.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

SKD_LAYERS = ("semantic", "kinetic", "dynamic")

_MODEL = "gemini-2.5-flash"

_SYSTEM_PROMPT = (
    "You are a knowledge-structure extractor. Given a QUESTION and its ANSWER, "
    "decompose the answer into Subject-Predicate-Object (S-P-O) triples and "
    "assign each triple to exactly one layer: "
    "'semantic' (what something IS: definition, attribute, classification), "
    "'kinetic' (what something DOES: action, process, transition), or "
    "'dynamic' (how something CHANGES: cause, effect, temporal evolution). "
    "Also list the distinct entities (concepts, actors, actions, attributes, "
    "causes, effects) and the relations between them, marking causal links "
    "explicitly. Respond with JSON only and no markdown fences. The JSON must "
    "have this exact shape: "
    '{"triples":[{"subject":str,"predicate":str,"object":str,"layer":"semantic|kinetic|dynamic"}],'
    '"entities":[{"id":str,"label":str,"kind":"concept|entity|action|attribute|cause|effect"}],'
    '"relations":[{"source":str,"target":str,"label":str,"kind":"causal|attribute|hierarchical|associative"}]}. '
    "Keep ids short and unique (e.g. e0, e1, e2...). "
    "IMPORTANT: inside the triples, 'subject' and 'object' MUST be the actual "
    "short readable text (e.g. '인공지능', '문제 해결'), NOT entity ids. "
    "Entity ids are used only in 'entities' and in 'relations' source/target."
)


def _normalise(result: dict[str, Any]) -> dict[str, Any]:
    triples = []
    for t in result.get("triples", [])[:60]:
        if not isinstance(t, dict):
            continue
        s = str(t.get("subject", "")).strip()
        p = str(t.get("predicate", "")).strip()
        o = str(t.get("object", "")).strip()
        layer = str(t.get("layer", "semantic")).strip().lower()
        if not (s and p and o):
            continue
        if layer not in SKD_LAYERS:
            layer = "semantic"
        triples.append({"subject": s, "predicate": p, "object": o, "layer": layer})

    entities = []
    seen_ids = set()
    for e in result.get("entities", [])[:120]:
        if not isinstance(e, dict):
            continue
        eid = str(e.get("id", "")).strip() or f"e{len(entities)}"
        label = str(e.get("label", "")).strip()
        kind = str(e.get("kind", "concept")).strip().lower()
        if not label or eid in seen_ids:
            continue
        seen_ids.add(eid)
        entities.append({"id": eid, "label": label, "kind": kind})

    entity_ids = {e["id"] for e in entities}
    relations = []
    for r in result.get("relations", [])[:120]:
        if not isinstance(r, dict):
            continue
        source = str(r.get("source", "")).strip()
        target = str(r.get("target", "")).strip()
        label = str(r.get("label", "")).strip()
        kind = str(r.get("kind", "associative")).strip().lower()
        if not (source and target):
            continue
        relations.append({"source": source, "target": target, "label": label or "relates", "kind": kind})

    # Resolve entity-id references inside triples back to readable labels.
    id_to_label = {e["id"]: e["label"] for e in entities}
    for t in triples:
        if t["subject"] in id_to_label:
            t["subject"] = id_to_label[t["subject"]]
        if t["object"] in id_to_label:
            t["object"] = id_to_label[t["object"]]

    return _attach_layers_and_topics({"triples": triples, "entities": entities, "relations": relations})


def _attach_layers_and_topics(data: dict[str, Any]) -> dict[str, Any]:
    """Derive topic grouping and propagate SKD layers to entities/relations."""
    triples = data["triples"]
    entities = data["entities"]
    relations = data["relations"]

    # Map entity label -> dominant layer from the triples it participates in.
    label_layer: dict[str, str] = {}
    for t in triples:
        label_layer.setdefault(t["subject"], t["layer"])
        label_layer.setdefault(t["object"], t["layer"])

    for e in entities:
        e["layer"] = label_layer.get(e["label"], "semantic")

    id_to_label = {e["id"]: e["label"] for e in entities}
    for r in relations:
        s_label = id_to_label.get(r["source"], "")
        t_label = id_to_label.get(r["target"], "")
        r["layer"] = label_layer.get(s_label) or label_layer.get(t_label) or "semantic"

    # Topic grouping: distinct subjects (topics), each with its objects.
    topics: dict[str, dict[str, Any]] = {}
    for t in triples:
        topic = topics.setdefault(
            t["subject"],
            {"label": t["subject"], "layer": t["layer"], "objects": []},
        )
        topic["objects"].append({
            "object": t["object"],
            "predicate": t["predicate"],
            "layer": t["layer"],
        })
    data["topics"] = [
        {"label": k, "layer": v["layer"], "objects": v["objects"]}
        for k, v in topics.items()
    ]
    return data


def _heuristic(question: str, answer: str) -> dict[str, Any]:
    """Fallback: flat S-P-O per sentence without calling the model."""
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", answer) if s.strip()]
    triples: list[dict[str, str]] = []
    entities = [{"id": "q0", "label": (question or "Question")[:160], "kind": "concept"}]
    relations: list[dict[str, str]] = []
    for i, sent in enumerate(sentences[:24]):
        sid = f"t{i}"
        triples.append({"subject": "Answer", "predicate": "states", "object": sent, "layer": "semantic"})
        entities.append({"id": sid, "label": sent[:140], "kind": "concept"})
        relations.append({"source": "q0", "target": sid, "label": "answers", "kind": "hierarchical"})
    return _attach_layers_and_topics({"triples": triples, "entities": entities, "relations": relations})


def _api_key() -> str:
    return os.environ.get("GEMINI_API_KEY", "").strip()


def build_mindmap_data(question: str, answer: str) -> dict[str, Any]:
    """Return mindmap / knowledge-graph data for a question and its answer.

    Uses the configured Gemini key when available; otherwise falls back to a
    simple sentence-level decomposition so the visualization always renders.
    """
    question = (question or "").strip()
    answer = (answer or "").strip()

    base: dict[str, Any] = {
        "question": question,
        "answer": answer,
        "topics": [],
        "triples": [],
        "entities": [],
        "relations": [],
    }

    if not answer:
        return base

    key = _api_key()
    if key:
        try:
            from core.llm import generate_json
            parsed = generate_json(
                _SYSTEM_PROMPT,
                (
                    f"QUESTION:\n{question or '(none)'}\n\nANSWER:\n{answer}\n\n"
                    "Extract the S-P-O triples (with SKD layers), entities, and relations."
                ),
                timeout_s=timeout_seconds,
            )
            data = _normalise(parsed if isinstance(parsed, dict) else {})
            base.update(data)
            return base
        except Exception:
            pass

    base.update(_heuristic(question, answer))
    return base
