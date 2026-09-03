"""Multi-Agent Swarm Orchestration & DAG Task Engine for WEAID AGI."""
from __future__ import annotations

import asyncio
import copy
import json
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable

BASE_DIR = Path(__file__).resolve().parent.parent


class AgentRole(str, Enum):
    PLANNER = "planner"
    CODER = "coder"
    RESEARCHER = "researcher"
    CRITIC = "critic"
    FACT_CHECKER = "fact_checker"
    EXECUTOR = "executor"


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class SubTask:
    id: str
    title: str
    role: AgentRole
    description: str = ""
    dependencies: list[str] = field(default_factory=list)
    input_data: dict[str, Any] = field(default_factory=dict)
    status: TaskStatus = TaskStatus.PENDING
    output: Any = None
    error: str = ""
    thoughts: list[str] = field(default_factory=list)
    started_at: float = 0.0
    finished_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["role"] = self.role.value
        result["status"] = self.status.value
        return result


class TaskGraph:
    """DAG of interconnected sub-tasks with dependency resolution."""

    def __init__(self):
        self.tasks: dict[str, SubTask] = {}

    def add_task(self, task: SubTask) -> None:
        self.tasks[task.id] = task

    def get_ready_tasks(self) -> list[SubTask]:
        ready = []
        for task in self.tasks.values():
            if task.status == TaskStatus.PENDING:
                deps_done = all(
                    self.tasks[dep_id].status == TaskStatus.COMPLETED
                    for dep_id in task.dependencies
                    if dep_id in self.tasks
                )
                if deps_done:
                    ready.append(task)
        return ready

    def is_finished(self) -> bool:
        return all(t.status in (TaskStatus.COMPLETED, TaskStatus.FAILED) for t in self.tasks.values())


async def _execute_sub_agent(
    task: SubTask,
    context: dict[str, Any],
    progress_cb: Callable[[SubTask], None] | None = None,
) -> Any:
    """Execute a specialized sub-agent role for a given subtask."""
    from core.llm import generate
    from core.codeact_engine import CodeActEngine

    task.status = TaskStatus.RUNNING
    task.started_at = time.monotonic()
    if progress_cb:
        progress_cb(task)

    deps_outputs = {dep_id: context.get(dep_id, {}) for dep_id in task.dependencies}
    ctx_str = json.dumps(deps_outputs, ensure_ascii=False, indent=2)

    try:
        if task.role == AgentRole.CODER:
            task.thoughts.append(f"[Coder] Synthesizing code for {task.title}")
            engine = CodeActEngine()
            res = engine.run_codeact(
                goal=f"{task.title}\nDescription: {task.description}",
                context=deps_outputs,
            )
            if res.success:
                task.thoughts.append(f"[Coder] Successfully executed (attempts={res.attempts})")
                task.output = res.to_dict()
                task.status = TaskStatus.COMPLETED
            else:
                task.thoughts.append(f"[Coder] Failed: {res.error}")
                task.error = res.error
                task.status = TaskStatus.FAILED

        elif task.role == AgentRole.CRITIC:
            task.thoughts.append(f"[Critic] Evaluating compliance for {task.title}")
            sys_prompt = "You are the Critic Agent for WEAID AGI. Identify flaws, safety violations, and compliance."
            usr_prompt = f"Evaluate this task result:\nTask: {task.title}\nContext / Deps:\n{ctx_str}\nExplain judgment and output 'CRITIC_PASSED' or 'CRITIC_FAILED'."
            eval_res = generate(sys_prompt, usr_prompt, timeout_s=30)
            task.output = eval_res.strip()
            task.thoughts.append("[Critic] Review completed")
            task.status = TaskStatus.COMPLETED

        elif task.role == AgentRole.RESEARCHER:
            task.thoughts.append(f"[Researcher] Synthesizing domain insights for {task.title}")
            sys_prompt = "You are the Researcher Agent for WEAID AGI. Provide concise, factual research summaries."
            usr_prompt = f"Research topic: {task.title}\n{task.description}\nPrior context:\n{ctx_str}"
            res = generate(sys_prompt, usr_prompt, timeout_s=30)
            task.output = res.strip()
            task.thoughts.append("[Researcher] Insight synthesis completed")
            task.status = TaskStatus.COMPLETED

        else:
            task.thoughts.append(f"[Executor] Executing {task.title}")
            sys_prompt = "You are a specialized AGI sub-agent."
            usr_prompt = f"Perform task: {task.title}\nContext:\n{ctx_str}"
            res = generate(sys_prompt, usr_prompt, timeout_s=30)
            task.output = res.strip()
            task.status = TaskStatus.COMPLETED

    except Exception as e:
        task.status = TaskStatus.FAILED
        task.error = str(e)
        task.thoughts.append(f"[Error] {str(e)}")
    finally:
        task.finished_at = time.monotonic()
        if progress_cb:
            progress_cb(task)

    return task.output


class SwarmOrchestrator:
    """Orchestrates goal decomposition and parallel DAG sub-agent execution."""

    def decompose_goal(self, goal: str, context: dict[str, Any] | None = None) -> TaskGraph:
        """Use LLM to decompose a complex goal into a DAG of SubTasks."""
        from core.llm import generate_json

        sys_prompt = (
            "You are the Swarm Planner Agent for WEAID AGI.\n"
            "Decompose the user's goal into 2-5 interconnected subtasks with dependencies.\n"
            "Available roles: 'researcher', 'coder', 'critic', 'fact_checker', 'executor'.\n"
            "Return a JSON object with format:\n"
            "{\"tasks\": [{\"id\": \"t1\", \"title\": \"...\", \"role\": \"coder\", \"description\": \"...\", \"dependencies\": []}, ...]}"
        )

        usr_prompt = f"User Goal: {goal}\n\nDecompose into tasks JSON:"
        graph = TaskGraph()

        try:
            raw_plan = generate_json(sys_prompt, usr_prompt, timeout_s=30)
            tasks_list = raw_plan.get("tasks", [])
            for item in tasks_list:
                role_str = item.get("role", "executor").lower()
                try:
                    role = AgentRole(role_str)
                except ValueError:
                    role = AgentRole.EXECUTOR

                subtask = SubTask(
                    id=item.get("id", f"t_{len(graph.tasks) + 1}"),
                    title=item.get("title", "action"),
                    role=role,
                    description=item.get("description", ""),
                    dependencies=item.get("dependencies", []),
                )
                graph.add_task(subtask)
        except Exception:
            # Fallback single executor task
            graph.add_task(SubTask(
                id="t1",
                title=goal,
                role=AgentRole.CODER,
                description=goal,
            ))

        return graph

    async def execute_graph_async(
        self,
        graph: TaskGraph,
        progress_cb: Callable[[SubTask], None] | None = None,
    ) -> dict[str, Any]:
        """Run ready tasks concurrently until completion."""
        context: dict[str, Any] = {}

        while not graph.is_finished():
            ready = graph.get_ready_tasks()
            if not ready:
                break

            coros = [_execute_sub_agent(task, context, progress_cb) for task in ready]
            results = await asyncio.gather(*coros, return_exceptions=True)

            for task, res in zip(ready, results):
                if isinstance(res, Exception):
                    task.status = TaskStatus.FAILED
                    task.error = str(res)
                else:
                    context[task.id] = task.output

        return {
            "tasks": {id: t.to_dict() for id, t in graph.tasks.items()},
            "success": all(t.status == TaskStatus.COMPLETED for t in graph.tasks.values()),
            "context": context,
        }

    def execute_graph(
        self,
        graph: TaskGraph,
        progress_cb: Callable[[SubTask], None] | None = None,
    ) -> dict[str, Any]:
        """Synchronous wrapper for graph execution."""
        return asyncio.run(self.execute_graph_async(graph, progress_cb))

