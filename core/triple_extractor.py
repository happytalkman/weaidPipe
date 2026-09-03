"""Conservative, local triple proposals from a conversational turn."""

from __future__ import annotations

import re

_PATTERNS = (
    (re.compile(r"([^.!?\n]{2,40})\s*(?:은|는|이|가)\s*([^.!?\n]{2,80})"), "is", "semantic"),
    (re.compile(r"([^.!?\n]{2,40})\s*(?:을|를)\s*([^.!?\n]{2,80})\s*(?:예약|추가|기록)"), "performs", "kinetic"),
)


def extract_triples_from_turn(question: str, answer: str) -> list[dict[str, str]]:
    text = f"{question}\n{answer}"
    triples: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for pattern, predicate, layer in _PATTERNS:
        for match in pattern.finditer(text):
            subject = re.sub(r"\s+", " ", match.group(1)).strip(" ,")
            obj = re.sub(r"\s+", " ", match.group(2)).strip(" ,")
            key = (subject, predicate, obj)
            if key not in seen and len(subject) >= 2 and len(obj) >= 2:
                seen.add(key)
                triples.append({"subject": subject, "predicate": predicate, "object": obj, "layer": layer})
    return triples[:20]