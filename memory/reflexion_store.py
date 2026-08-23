"""Neuro-Symbolic Reflexion Memory for Continuous Self-Improvement."""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_REFLEXION_PATH = BASE_DIR / "memory" / "reflexion_store.json"


@dataclass
class ReflexionItem:
    id: str
    goal: str
    error_summary: str
    root_cause: str
    corrective_strategy: str
    triples: list[dict[str, str]] = field(default_factory=list)
    fixed_code_snippet: str = ""
    created_at: float = field(default_factory=time.time)
    occurrence_count: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ReflexionStore:
    """Stores causal reflections from past failures and injects lessons into future reasoning."""

    def __init__(self, store_path: Path | None = None):
        self.store_path = store_path or DEFAULT_REFLEXION_PATH
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        self._items: dict[str, ReflexionItem] = {}
        self._load()

    def _load(self) -> None:
        if self.store_path.exists():
            try:
                data = json.loads(self.store_path.read_text(encoding="utf-8"))
                for k, v in data.items():
                    self._items[k] = ReflexionItem(**v)
            except Exception:
                self._items = {}

    def _save(self) -> None:
        data = {k: v.to_dict() for k, v in self._items.items()}
        self.store_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def synthesize_reflexion(
        self,
        goal: str,
        failed_code: str,
        error_msg: str,
        fixed_code: str,
    ) -> ReflexionItem:
        """Analyze a failure-and-fix sequence and synthesize symbolic S-P-O knowledge."""
        from core.llm import generate_json

        sys_prompt = (
            "You are the Neuro-Symbolic Reflexion Engine for WEAID AGI.\n"
            "Analyze the failure and its working resolution to formulate causal rules and ontology triples.\n"
            "Return JSON format:\n"
            "{\n"
            "  \"root_cause\": \"...\",\n"
            "  \"corrective_strategy\": \"...\",\n"
            "  \"triples\": [{\"subject\": \"...\", \"predicate\": \"causes / resolves / requires\", \"object\": \"...\"}]\n"
            "}"
        )

        usr_prompt = (
            f"Goal: {goal}\n\n"
            f"Failed Code:\n```python\n{failed_code}\n```\n\n"
            f"Error Traceback:\n{error_msg}\n\n"
            f"Fixed Code:\n```python\n{fixed_code}\n```\n\n"
            "Synthesize reflexion JSON:"
        )

        item_id = f"ref_{int(time.time() * 1000)}"
        try:
            res = generate_json(sys_prompt, usr_prompt, timeout_s=30)
            item = ReflexionItem(
                id=item_id,
                goal=goal,
                error_summary=error_msg[:120],
                root_cause=res.get("root_cause", "Execution error"),
                corrective_strategy=res.get("corrective_strategy", "Apply fixed implementation"),
                triples=res.get("triples", []),
                fixed_code_snippet=fixed_code[:300],
            )
        except Exception:
            item = ReflexionItem(
                id=item_id,
                goal=goal,
                error_summary=error_msg[:120],
                root_cause="Execution exception",
                corrective_strategy="Inspect traceback and fix syntax/imports",
                triples=[{"subject": goal, "predicate": "resolves_with", "object": "bugfix"}],
                fixed_code_snippet=fixed_code[:300],
            )

        self._items[item_id] = item
        self._save()
        return item

    def find_relevant_reflections(self, query: str, limit: int = 3) -> list[ReflexionItem]:
        """Retrieve relevant past lessons based on keyword match."""
        q_words = set(query.lower().split())
        scored: list[tuple[int, ReflexionItem]] = []

        for item in self._items.values():
            match_score = 0
            text_corpus = f"{item.goal} {item.root_cause} {item.corrective_strategy}".lower()
            for w in q_words:
                if len(w) > 2 and w in text_corpus:
                    match_score += 1
            if match_score > 0:
                scored.append((match_score, item))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in scored[:limit]]

    def list_all(self) -> list[ReflexionItem]:
        return list(self._items.values())

