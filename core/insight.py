"""Combined post-answer insight pipeline (원콜 인사이트 파이프라인).

The old flow made TWO sequential Gemini calls after every answer
(1: mindmap S-P-O extraction, 2: constitutional review), which doubled the
latency and desynced the mindmap/ontology from the answer.

This module fuses both into a SINGLE Gemini call that returns:

  * S-P-O triples with SKD layers, topics, entities, relations  (mindmap/ontology)
  * constitutional review: score, per-principle checks, issues, revised answer

One call → both artifacts arrive together → mindmap & ontology are always in
sync with the answer.
"""
from __future__ import annotations

import json
import os
from typing import Any

from core.constitution import CONSTITUTION
from core.mindmap import _attach_layers_and_topics, _heuristic, _normalise

_MODEL = "gemini-2.5-flash"

_SYSTEM_PROMPT = (
    "You are the WEAID insight engine. Given a QUESTION and its ANSWER, do BOTH "
    "jobs in one response: "
    "(1) Decompose the answer into Subject-Predicate-Object (S-P-O) triples with "
    "a layer each — 'semantic' (what something IS), 'kinetic' (what something "
    "DOES), 'dynamic' (how something CHANGES) — plus entities and relations "
    "(mark causal links). "
    "(2) Review the answer against the CONSTITUTION and score it. "
    "Respond with JSON only, no markdown fences. Exact shape: "
    '{"triples":[{"subject":str,"predicate":str,"object":str,"layer":"semantic|kinetic|dynamic"}],'
    '"entities":[{"id":str,"label":str,"kind":"concept|entity|action|attribute|cause|effect"}],'
    '"relations":[{"source":str,"target":str,"label":str,"kind":"causal|attribute|hierarchical|associative"}],'
    '"review":{"score":int0to100,"compliant":bool,'
    '"checks":[{"id":str,"pass":bool,"note":str}],'
    '"issues":[str],"revised":str}} . '
    "In triples, subject/object MUST be short readable text, not ids. "
    "checks must cover every constitution id exactly once. revised is the "
    "rewritten response only when there are violations, else empty string."
)


def _api_key() -> str:
    return os.environ.get("GEMINI_API_KEY", "").strip()


def _norm_review(review: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "score": 100,
        "compliant": True,
        "checks": [{"id": p["id"], "pass": True, "note": ""} for p in CONSTITUTION],
        "issues": [],
        "revised": "",
    }
    if not isinstance(review, dict):
        return base
    try:
        score = max(0, min(100, int(review.get("score", 100))))
    except (TypeError, ValueError):
        score = 100
    ids = {p["id"] for p in CONSTITUTION}
    seen: set[str] = set()
    checks: list[dict[str, Any]] = []
    for c in review.get("checks") or []:
        if not isinstance(c, dict):
            continue
        cid = str(c.get("id", "")).strip()
        if cid not in ids or cid in seen:
            continue
        seen.add(cid)
        checks.append({"id": cid, "pass": bool(c.get("pass", True)), "note": str(c.get("note", ""))[:200]})
    for p in CONSTITUTION:
        if p["id"] not in seen:
            checks.append({"id": p["id"], "pass": True, "note": ""})
    issues = [str(i)[:200] for i in (review.get("issues") or [])]
    revised = str(review.get("revised", "")).strip()
    compliant = bool(review.get("compliant", not issues and score >= 80))
    return {
        "score": score,
        "compliant": compliant,
        "checks": checks,
        "issues": issues,
        "revised": revised if not compliant and revised else "",
    }


def build_insight(question: str, answer: str, timeout_seconds: float = 45.0) -> dict[str, Any]:
    """One call → {mindmap fields..., review: {...}} for a Q&A pair."""
    question = (question or "").strip()
    answer = (answer or "").strip()

    review_default = _norm_review({})
    base: dict[str, Any] = {
        "question": question,
        "answer": answer,
        "topics": [],
        "triples": [],
        "entities": [],
        "relations": [],
        "review": review_default,
    }
    if not answer:
        return base

    key = _api_key()
    if not key:
        base.update(_heuristic(question, answer))
        return base

    try:
        from core.llm import generate_json
        parsed = generate_json(
            _SYSTEM_PROMPT,
            (
                f"CONSTITUTION:\n{json.dumps(CONSTITUTION, ensure_ascii=False)}\n\n"
                f"QUESTION:\n{question or '(voice)'}\n\nANSWER:\n{answer}"
            ),
            timeout_s=timeout_seconds,
        )
        if not isinstance(parsed, dict):
            raise ValueError("bad insight payload")
        data = _normalise(parsed)
        base.update(data)
        base["review"] = _norm_review(parsed.get("review"))
        return base
    except Exception:
        base.update(_heuristic(question, answer))
        return base
