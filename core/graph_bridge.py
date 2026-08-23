"""Bidirectional Graph Bridge connecting memory & reflexion to UI Knowledge Graph."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core import graph_store
from memory.reflexion_store import ReflexionItem, ReflexionStore


class GraphBridge:
    """Synchronizes symbolic Reflexion knowledge and conversation entities into the Knowledge Graph."""

    def __init__(self, reflexion_store: ReflexionStore | None = None):
        self.reflexion_store = reflexion_store or ReflexionStore()

    def sync_reflexion_to_graph(self, item: ReflexionItem) -> dict[str, Any]:
        """Inject triples from a ReflexionItem into the shared GraphStore."""
        store = graph_store.load()
        triples_payload = []
        for t in item.triples:
            s, p, o = t.get("subject", ""), t.get("predicate", ""), t.get("object", "")
            if s and p and o:
                triples_payload.append({
                    "subject": s,
                    "predicate": p,
                    "object": o,
                    "layer": "semantic",
                })

        if triples_payload:
            store = graph_store.merge_insight(store, {"triples": triples_payload})
            graph_store.save(store)
            graph_store.publish(store)

        return store

    def get_focused_graph_for_query(self, query: str) -> dict[str, Any]:
        """Retrieve sub-graph and relevant reflections for a user query."""
        reflections = self.reflexion_store.find_relevant_reflections(query)
        store = graph_store.load()
        return {
            "graph": store,
            "reflections": [r.to_dict() for r in reflections],
            "query": query,
        }


