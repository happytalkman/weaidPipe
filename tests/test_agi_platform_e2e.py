"""End-to-End integration test suite for the complete WEAID AGI Platform."""
import tempfile
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from core.codeact_engine import CodeActEngine
from actions.toolcraft import ToolCrafter
from agent.swarm import AgentRole, SubTask, TaskGraph, TaskStatus, SwarmOrchestrator
from awareness.vision_observer import ScreenFrame, VisionObserver
from awareness.proactive_agent import ProactiveAdvisor
from memory.reflexion_store import ReflexionStore
from core.graph_bridge import GraphBridge


def test_complete_agi_workflow_pipeline():
    """
    Test full end-to-end AGI pipeline:
    1. Swarm decomposes user goal
    2. CodeAct synthesizes and executes Python logic
    3. ToolCrafter promotes verified code to permanent custom tool
    4. Swarm Critic validates execution result
    5. ReflexionStore records causal learning
    6. GraphBridge syncs triples to Knowledge Graph
    7. ProactiveAdvisor observes screen context and generates proactive next step
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        temp_dir = Path(tmpdir)
        tools_dir = temp_dir / "custom_tools"
        reflexion_path = temp_dir / "reflexion.json"

        # 1. Swarm Orchestrator & Goal Decomposition
        orchestrator = SwarmOrchestrator()
        mock_plan = {
            "tasks": [
                {
                    "id": "t1",
                    "title": "Analyze and Parse Log Metrics",
                    "role": "coder",
                    "description": "Calculate error frequency and average response latency",
                    "dependencies": [],
                },
                {
                    "id": "t2",
                    "title": "Critic and Verify Results",
                    "role": "critic",
                    "description": "Validate safety and numerical bounds",
                    "dependencies": ["t1"],
                },
            ]
        }

        with patch("core.llm.generate_json", return_value=mock_plan):
            graph = orchestrator.decompose_goal("Analyze system performance logs and verify stability")
            assert len(graph.tasks) == 2

        # 2. CodeAct Execution
        engine = CodeActEngine()
        code_snippet = """
def run(req_count=100, error_count=2):
    error_rate = (error_count / req_count) * 100
    print(f"ERROR_RATE:{error_rate}%")
    return {"error_rate": error_rate, "status": "HEALTHY"}

if __name__ == '__main__':
    run()
"""
        codeact_res = engine.execute_code(code_snippet)
        assert codeact_res.success is True
        assert "ERROR_RATE:2.0%" in codeact_res.output

        # 3. ToolCrafter: Promote to Permanent Tool
        crafter = ToolCrafter(tools_dir=tools_dir)
        tool = crafter.register_tool(
            name="system_health_analyzer",
            description="Computes error rate and cluster health status",
            parameters={"req_count": "integer", "error_count": "integer"},
            code=code_snippet,
            tags=["analytics", "monitoring"],
        )
        assert crafter.get_tool("system_health_analyzer") is not None
        tool_res = crafter.execute_tool("system_health_analyzer", {"req_count": 500, "error_count": 5})
        assert tool_res["error_rate"] == 1.0

        # 4. Swarm Execution with Critic
        with patch("core.codeact_engine.CodeActEngine.run_codeact") as mock_codeact, \
             patch("core.llm.generate", return_value="CRITIC_PASSED: Metrics within safe operating threshold."):
            mock_res_obj = MagicMock()
            mock_res_obj.success = True
            mock_res_obj.attempts = 1
            mock_res_obj.to_dict.return_value = {"output": "HEALTHY", "success": True}
            mock_codeact.return_value = mock_res_obj

            swarm_result = orchestrator.execute_graph(graph)
            assert swarm_result["success"] is True
            assert "CRITIC_PASSED" in swarm_result["context"]["t2"]

        # 5. Neuro-Symbolic Reflexion & Knowledge Graph Sync
        reflexion_store = ReflexionStore(store_path=reflexion_path)
        bridge = GraphBridge(reflexion_store=reflexion_store)

        mock_reflexion = {
            "root_cause": "High concurrent requests on unindexed log table",
            "corrective_strategy": "Create index on timestamp column and cache metrics",
            "triples": [
                {"subject": "log_table", "predicate": "needs_index_on", "object": "timestamp"},
                {"subject": "system_health_analyzer", "predicate": "monitors", "object": "cluster_stability"},
            ],
        }

        with patch("core.llm.generate_json", return_value=mock_reflexion):
            ref_item = reflexion_store.synthesize_reflexion(
                goal="Optimize log processing latency",
                failed_code="SELECT * FROM logs",
                error_msg="QueryTimeoutError: lock wait timeout exceeded",
                fixed_code="SELECT * FROM logs WHERE ts > NOW() - INTERVAL 1 HOUR",
            )
            assert len(ref_item.triples) == 2
            sync_res = bridge.sync_reflexion_to_graph(ref_item)
            assert "triples" in sync_res

        # 6. Proactive Ambient Vision & Context Observer
        advisor = ProactiveAdvisor(cooldown_seconds=0.0)
        frame = ScreenFrame(
            timestamp=time.time(),
            window_title="Grafana Dashboard - Alerting Threshold",
            image_bytes=b"sample_frame_data",
            frame_hash="hash_dashboard",
        )

        mock_proposal = {
            "title": "Apply Log Index Optimization",
            "description": "Observed high query load on monitoring dashboard",
            "suggested_action": "Execute auto-index migration",
            "confidence": 0.92,
            "trigger_reason": "High latency alert observed on screen",
        }

        with patch("core.llm.generate_json", return_value=mock_proposal):
            proposal = advisor.evaluate_context(frame, text_context="Grafana high load", force=True)
            assert proposal is not None
            assert proposal.title == "Apply Log Index Optimization"
            assert proposal.confidence == 0.92

