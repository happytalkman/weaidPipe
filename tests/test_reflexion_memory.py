"""Unit tests for Phase 4: Neuro-Symbolic Reflexion Memory & Graph Bridge."""
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from memory.reflexion_store import ReflexionStore, ReflexionItem
from core.graph_bridge import GraphBridge


@pytest.fixture
def temp_store_path():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        path = Path(tmp.name)
    yield path
    if path.exists():
        path.unlink()


def test_reflexion_store_synthesis(temp_store_path):
    store = ReflexionStore(store_path=temp_store_path)

    mock_llm_res = {
        "root_cause": "Missing UTF-8 parameter in open() call on Windows",
        "corrective_strategy": "Always pass encoding='utf-8' to open()",
        "triples": [
            {"subject": "open_file", "predicate": "requires_encoding", "object": "utf-8"},
            {"subject": "windows_cp949", "predicate": "causes_error_without", "object": "utf-8"},
        ],
    }

    with patch("core.llm.generate_json", return_value=mock_llm_res):
        item = store.synthesize_reflexion(
            goal="Read korean text file",
            failed_code="open('data.txt').read()",
            error_msg="UnicodeDecodeError: 'cp949' codec can't decode byte",
            fixed_code="open('data.txt', encoding='utf-8').read()",
        )

        assert item.root_cause == "Missing UTF-8 parameter in open() call on Windows"
        assert len(item.triples) == 2
        assert len(store.list_all()) == 1


def test_reflexion_retrieval(temp_store_path):
    store = ReflexionStore(store_path=temp_store_path)
    store._items["ref_1"] = ReflexionItem(
        id="ref_1",
        goal="Parse JSON response from API",
        error_summary="JSONDecodeError",
        root_cause="API returned HTML error page",
        corrective_strategy="Check response status code before json.loads",
        triples=[{"subject": "json_parser", "predicate": "needs", "object": "status_check"}],
    )
    store._items["ref_2"] = ReflexionItem(
        id="ref_2",
        goal="Connect to Postgres database",
        error_summary="ConnectionRefusedError",
        root_cause="Port 5432 is blocked",
        corrective_strategy="Verify Docker container port forwarding",
        triples=[{"subject": "db_conn", "predicate": "needs", "object": "port_mapping"}],
    )

    results = store.find_relevant_reflections("JSON parsing API failure")
    assert len(results) >= 1
    assert results[0].id == "ref_1"


def test_graph_bridge_sync(temp_store_path):
    store = ReflexionStore(store_path=temp_store_path)
    bridge = GraphBridge(reflexion_store=store)

    item = ReflexionItem(
        id="ref_test",
        goal="Audio buffer overflow",
        error_summary="BufferOverflowError",
        root_cause="Sampling rate mismatch",
        corrective_strategy="Resample to 16000Hz",
        triples=[
            {"subject": "microphone_stream", "predicate": "requires_samplerate", "object": "16000Hz"},
        ],
    )

    result_graph = bridge.sync_reflexion_to_graph(item)
    assert "triples" in result_graph
    assert len(result_graph["triples"]) >= 1

    focused = bridge.get_focused_graph_for_query("microphone sampling")
    assert "graph" in focused
    assert "reflections" in focused

