"""워크플로우 루틴 — 한 마디로 다중 동작 실행.

"아침 루틴" 같은 이름 붙은 단계 목록을 저장하고, 실행 시 각 단계를
순차 지시로 변환한다. 순수 함수 위주 (테스트 가능).

저장: memory/routines.json
"""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
ROUTINES_FILE = BASE_DIR / "memory" / "routines.json"

BUILTIN_ROUTINES: dict[str, list[str]] = {
    "아침 루틴": [
        "오늘 날씨를 알려줘",
        "주요 뉴스 3개를 요약해줘",
        "오늘 예약된 일정이 있으면 알려줘",
    ],
    "퇴근 루틴": [
        "오늘 대화를 요약해줘",
        "내일 예약된 일정을 확인해줘",
    ],
}


def load_routines() -> dict[str, list[str]]:
    routines = dict(BUILTIN_ROUTINES)
    try:
        if ROUTINES_FILE.exists():
            data = json.loads(ROUTINES_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for name, steps in data.items():
                    if isinstance(steps, list):
                        routines[str(name)] = [str(s)[:200] for s in steps]
    except Exception:
        pass
    return routines


def save_routines(routines: dict[str, list[str]]) -> Path:
    ROUTINES_FILE.parent.mkdir(parents=True, exist_ok=True)
    ROUTINES_FILE.write_text(json.dumps(routines, ensure_ascii=False, indent=2), encoding="utf-8")
    return ROUTINES_FILE


def define_routine(name: str, steps: list[str]) -> dict[str, list[str]]:
    """루틴 추가/갱신."""
    routines = load_routines()
    routines[str(name).strip()[:40]] = [str(s).strip()[:200] for s in steps if str(s).strip()]
    save_routines(routines)
    return routines


def remove_routine(name: str) -> bool:
    routines = load_routines()
    if name not in routines:
        return False
    del routines[name]
    save_routines(routines)
    return True


def expand_routine(routines: dict[str, list[str]], name: str) -> list[str]:
    """루틴 이름 → 단계 목록."""
    return list(routines.get(name, []))


def parse_routine_command(text: str) -> tuple[str | None, dict[str, Any]]:
    """('run'|'list'|'define'|'remove', payload) 해석."""
    t = " ".join(str(text or "").split())
    if not t:
        return None, {}
    if "루틴" not in t:
        return None, {}
    if any(k in t for k in ("목록", "보여줘", "리스트")):
        return "list", {}
    m = re.search(r"루틴\s*(?:만들어줘|추가해줘|생성해줘)\s*(?:이름[:：\s]*)?([^\s,，]+)(?:[,，\s]*(?:단계|스텝)[:：\s]*(.+))?", t)
    if m and any(k in t for k in ("만들", "추가", "생성")):
        steps = [s.strip() for s in re.split(r"[,，;；]", m.group(2) or "") if s.strip()]
        return "define", {"name": m.group(1), "steps": steps}
    m = re.search(r"루틴\s*(?:삭제|지워|제거)\s*(?:해줘|줘)?\s*([^\s]+)", t)
    if m:
        return "remove", {"name": m.group(1)}
    m = re.search(r"(.+?)\s*루틴\s*(?:실행|시작)(?:해줘|줘)?", t)
    if m:
        return "run", {"name": m.group(1).strip()}
    return None, {}
