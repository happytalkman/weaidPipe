"""에이전트 루프 (B3) — 계획→실행→검증 순환의 순수 로직.

실행부(도구 호출)는 통합 계층에서 처리하고, 여기서는 루프 제어와
검증 판정을 담당한다 (테스트 가능).
"""
from __future__ import annotations

from typing import Any, Callable


def should_continue(iteration: int, max_iterations: int, last_result: str, goal: str) -> bool:
    """한 번 더 반복할지 판정."""
    if iteration >= max_iterations:
        return False
    if not str(last_result or "").strip():
        return False
    # 목표 달성 신호 (간단 휴리스틱)
    done_markers = ("완료", "성공", "답:", "결과:")
    if any(m in str(last_result) for m in done_markers):
        return False
    return True


def plan_step(goal: str, tools: list[str], history: list[str]) -> str:
    """다음 단계 지시 생성 (LLM 없이 — 도구 목록 기반 안내)."""
    tools_line = ", ".join(tools) or "(도구 없음)"
    history_line = " | ".join(history[-3:]) or "(없음)"
    return (
        f"목표: {goal}\n"
        f"사용 가능 도구: {tools_line}\n"
        f"이전 결과: {history_line}\n"
        f"다음 도구 하나를 선택해 목표에 한 걸음 다가가세요. 완료 시 '답:'으로 시작해 요약하세요."
    )


def verify_result(result: str, goal: str) -> bool:
    """결과가 목표를 충족하는지 결정적 검증 (핵심어 포함 여부)."""
    r = str(result or "")
    if not r:
        return False
    tokens = [t for t in goal.split() if len(t) >= 2]
    if not tokens:
        return bool(r)
    return any(t in r for t in tokens[:5])


def run_loop(
    goal: str,
    tools: list[str],
    executor: Callable[[str], str],
    max_iterations: int = 5,
) -> dict[str, Any]:
    """계획→실행→검증 루프 실행 (executor 주입 — 테스트 용이)."""
    history: list[str] = []
    for i in range(1, max_iterations + 1):
        instruction = plan_step(goal, tools, history)
        result = executor(instruction)
        history.append(result)
        if verify_result(result, goal):
            return {"completed": True, "iterations": i, "result": result, "history": history}
        if not should_continue(i, max_iterations, result, goal):
            return {"completed": False, "iterations": i, "result": result, "history": history}
    return {"completed": False, "iterations": max_iterations, "result": (history[-1] if history else ""), "history": history}
