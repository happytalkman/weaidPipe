"""봇 스킬 실체화 (C1).

봇 프로필의 '스킬'을 실제 실행 가능한 도구에 바인딩한다.

  * SKILLS 레지스트리: 이름 → {설명, 핸들러}
  * execute_skill(name, args) — 안전 디스패치 (미등록/실패 시 오류 문자열)
  * available_skills(bot) — 봇이 가진 스킬 ∩ 레지스트리

내장 스킬: time / calculator / graph_search / echo
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Callable

# ── 내장 스킬 핸들러 ──────────────────────────────────────────────────────

def _skill_time(_args: str) -> str:
    return f"현재 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"


_CALC_RE = re.compile(r"^[0-9+\-*/(). %]+$")


def _skill_calculator(args: str) -> str:
    expr = str(args or "").strip()
    if not expr or not _CALC_RE.match(expr):
        return "오류: 계산식은 숫자와 + - * / ( ) 만 사용할 수 있습니다."
    try:
        value = eval(expr.replace("%", "/100"), {"__builtins__": {}})  # noqa: S307 (검증된 수식만)
    except Exception as e:
        return f"계산 오류: {str(e)[:80]}"
    return f"{expr} = {value}"


def _skill_graph_search(args: str) -> str:
    term = str(args or "").strip()
    if not term:
        return "오류: 검색어가 필요합니다 (예: 인공지능)."
    from core import graph_store
    result = graph_store.query(graph_store.load(), [term])
    matched = result.get("matched") or []
    if not matched:
        return f"'{term}' 관련 노드를 찾지 못했습니다."
    rels = result.get("relations") or []
    return f"'{term}' 관련 노드 {len(matched)}개: " + ", ".join(matched[:8]) + f" | 관계 {len(rels)}개"


def _skill_echo(args: str) -> str:
    return str(args or "")


SKILLS: dict[str, dict[str, Any]] = {
    "time": {"description": "현재 시각을 알려준다", "handler": _skill_time},
    "calculator": {"description": "산술 계산 (예: 12*3+4)", "handler": _skill_calculator},
    "graph_search": {"description": "대화 지식그래프에서 검색", "handler": _skill_graph_search},
    "echo": {"description": "입력을 그대로 반환", "handler": _skill_echo},
}


def register_skill(name: str, description: str, handler: Callable[[str], str]) -> None:
    """커스텀 스킬 등록."""
    SKILLS[name] = {"description": description, "handler": handler}


def available_skills(bot: dict[str, Any]) -> list[dict[str, str]]:
    """봇이 보유한 스킬 중 등록된 것의 (이름, 설명) 목록."""
    owned = set(bot.get("skills") or [])
    return [{"name": n, "description": s["description"]} for n, s in SKILLS.items() if n in owned]


def execute_skill(name: str, args: str = "") -> str:
    """스킬 실행 (안전 디스패치)."""
    skill = SKILLS.get((name or "").strip().lower())
    if skill is None:
        return f"오류: '{name}' 스킬은 등록되지 않았습니다. 등록된 스킬: {', '.join(SKILLS)}"
    try:
        return str(skill["handler"](args))
    except Exception as e:
        return f"스킬 실행 오류: {str(e)[:120]}"


def skill_descriptions(bot: dict[str, Any]) -> str:
    """봇 시스템 프롬프트에 넣을 스킬 설명."""
    skills = available_skills(bot)
    if not skills:
        return ""
    return "사용 가능한 스킬: " + "; ".join(f"{s['name']}({s['description']})" for s in skills)


def extract_skill_command(text: str) -> tuple[str, str] | None:
    """'봇 OO 스킬 [스킬명] [인자]' → (스킬명, 인자)."""
    m = re.search(r"스킬\s+([a-z_]+)(?:\s+(.+))?", str(text or ""))
    if not m:
        return None
    return m.group(1).lower(), (m.group(2) or "").strip()
