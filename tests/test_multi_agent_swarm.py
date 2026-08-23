"""Unit tests for Phase 2: Multi-Agent Swarm Orchestration & DAG Task Engine."""
import pytest
from unittest.mock import patch, MagicMock

from agent.swarm import AgentRole, SubTask, TaskGraph, TaskStatus, SwarmOrchestrator


def test_task_graph_dependency_resolution():
    graph = TaskGraph()
    t1 = SubTask(id="t1", title="Research", role=AgentRole.RESEARCHER)
    t2 = SubTask(id="t2", title="Code", role=AgentRole.CODER, dependencies=["t1"])
    t3 = SubTask(id="t3", title="Critic", role=AgentRole.CRITIC, dependencies=["t2"])

    graph.add_task(t1)
    graph.add_task(t2)
    graph.add_task(t3)

    # Initially only t1 should be ready
    ready = graph.get_ready_tasks()
    assert len(ready) == 1
    assert ready[0].id == "t1"

    # Complete t1 -> t2 becomes ready
    t1.status = TaskStatus.COMPLETED
    ready = graph.get_ready_tasks()
    assert len(ready) == 1
    assert ready[0].id == "t2"

    # Complete t2 -> t3 becomes ready
    t2.status = TaskStatus.COMPLETED
    ready = graph.get_ready_tasks()
    assert len(ready) == 1
    assert ready[0].id == "t3"

    # Complete t3 -> finished
    t3.status = TaskStatus.COMPLETED
    assert graph.is_finished() is True


def test_swarm_decompose_goal():
    orchestrator = SwarmOrchestrator()
    mock_plan = {
        "tasks": [
            {
                "id": "t1",
                "title": "Analyze Data Requirements",
                "role": "researcher",
                "description": "Examine input schemas",
                "dependencies": []
            },
            {
                "id": "t2",
                "title": "Write Processing Script",
                "role": "coder",
                "description": "Generate python data parser",
                "dependencies": ["t1"]
            },
            {
                "id": "t3",
                "title": "Review Quality",
                "role": "critic",
                "description": "Ensure output adherence",
                "dependencies": ["t2"]
            }
        ]
    }

    with patch("core.llm.generate_json", return_value=mock_plan):
        graph = orchestrator.decompose_goal("Process user logs and calculate stats")
        assert len(graph.tasks) == 3
        assert graph.tasks["t1"].role == AgentRole.RESEARCHER
        assert graph.tasks["t2"].role == AgentRole.CODER
        assert graph.tasks["t3"].role == AgentRole.CRITIC
        assert graph.tasks["t2"].dependencies == ["t1"]


def test_swarm_execute_graph_end_to_end():
    orchestrator = SwarmOrchestrator()
    graph = TaskGraph()

    t1 = SubTask(
        id="t1",
        title="Compute numbers",
        role=AgentRole.CODER,
        description="Print computed sum 42",
        dependencies=[],
    )
    t2 = SubTask(
        id="t2",
        title="Verify result",
        role=AgentRole.CRITIC,
        description="Check if sum is 42",
        dependencies=["t1"],
    )

    graph.add_task(t1)
    graph.add_task(t2)

    with patch("core.codeact_engine.CodeActEngine.run_codeact") as mock_codeact, \
         patch("core.llm.generate", return_value="CRITIC_PASSED: The calculation is exact."):
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.attempts = 1
        mock_result.to_dict.return_value = {"output": "42", "success": True}
        mock_codeact.return_value = mock_result

        updates = []
        def progress_tracker(task):
            updates.append((task.id, task.status))

        result = orchestrator.execute_graph(graph, progress_cb=progress_tracker)

        assert result["success"] is True
        assert graph.tasks["t1"].status == TaskStatus.COMPLETED
        assert graph.tasks["t2"].status == TaskStatus.COMPLETED
        assert "CRITIC_PASSED" in result["context"]["t2"]
        assert len(updates) >= 4

