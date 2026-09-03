"""Small, dependency-free SHACL-style validation for conversation triples."""

from __future__ import annotations

from typing import Any

ALLOWED_LAYERS = {"semantic", "kinetic", "dynamic"}
ALLOWED_PREDICATES = {
    "is", "has", "causes", "relatesTo", "isInstanceOf", "performs",
    "owns", "knows", "works_at", "scheduledFor", "supports",
}


def validate_triple(triple: dict[str, Any]) -> tuple[bool, str | None]:
    subject = str(triple.get("subject", "")).strip()
    predicate = str(triple.get("predicate", "")).strip()
    obj = str(triple.get("object", "")).strip()
    layer = str(triple.get("layer", "semantic")).strip().lower()
    if len(subject) < 2 or len(obj) < 2:
        return False, "subject and object must contain at least 2 characters"
    if predicate not in ALLOWED_PREDICATES:
        return False, f"predicate is not allowed: {predicate}"
    if layer not in ALLOWED_LAYERS:
        return False, f"layer is not allowed: {layer}"
    return True, None


def validate_batch(triples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results = []
    for triple in triples:
        valid, error = validate_triple(triple)
        results.append({"triple": triple, "valid": valid, "error": error})
    return results