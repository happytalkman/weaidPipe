"""사용자 정의 스킬 (JSON 플러그인) — D1.

코드 없이 JSON으로 스킬을 추가한다. 각 스킬은 답변 템플릿(type=reply)이며,
{args}로 인자를 치환한다. 앱 시작 시 bot_skills 레지스트리에 자동 등록된다.

저장: memory/custom_skills.json
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
SKILLS_FILE = BASE_DIR / "memory" / "custom_skills.json"


def load_custom_skills() -> list[dict[str, Any]]:
    try:
        if SKILLS_FILE.exists():
            data = json.loads(SKILLS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return [s for s in data if isinstance(s, dict) and s.get("name")]
    except Exception:
        pass
    return []


def save_custom_skills(skills: list[dict[str, Any]]) -> Path:
    SKILLS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SKILLS_FILE.write_text(json.dumps(skills, ensure_ascii=False, indent=2), encoding="utf-8")
    return SKILLS_FILE


def add_custom_skill(name: str, description: str, template: str) -> dict[str, Any]:
    """스킬 추가 (이름 중복 시 갱신). {args} 템플릿 지원."""
    skills = load_custom_skills()
    entry = {
        "name": str(name).strip().lower()[:40],
        "description": str(description).strip()[:120],
        "type": "reply",
        "template": str(template).strip()[:500],
    }
    skills = [s for s in skills if s.get("name") != entry["name"]]
    skills.append(entry)
    save_custom_skills(skills)
    return entry


def remove_custom_skill(name: str) -> bool:
    skills = load_custom_skills()
    remaining = [s for s in skills if s.get("name") != str(name).strip().lower()]
    if len(remaining) == len(skills):
        return False
    save_custom_skills(remaining)
    return True


def render_skill(skill: dict[str, Any], args: str) -> str:
    """템플릿 렌더링 ({args} 치환)."""
    template = str(skill.get("template", ""))
    return template.replace("{args}", str(args or "").strip())


def execute_custom_skill(name: str, args: str = "") -> str | None:
    """등록된 커스텀 스킬 실행. 미등록이면 None."""
    for skill in load_custom_skills():
        if skill.get("name") == str(name).strip().lower():
            return render_skill(skill, args)
    return None


def register_all() -> int:
    """모든 커스텀 스킬을 bot_skills 레지스트리에 등록. 등록 수 반환."""
    from core import bot_skills
    count = 0
    for skill in load_custom_skills():
        name = skill["name"]

        def make_handler(template: str):
            return lambda a: template.replace("{args}", str(a or "").strip())

        bot_skills.register_skill(name, skill.get("description", "커스텀 스킬"), make_handler(skill["template"]))
        count += 1
    return count


def parse_skill_define_command(text: str) -> dict[str, Any] | None:
    """'스킬 만들어줘 이름:OO 설명:OO 답변:OO' 해석 (키워드 세그먼트 분리)."""
    t = str(text or "")
    if "스킬" not in t or not any(k in t for k in ("만들", "추가")):
        return None

    def seg(keyword: str) -> str:
        m = re.search(keyword + r"[:：]\s*([^,，]+?)(?=\s*(?:설명|답변)[:：]|$)", t)
        return m.group(1).strip() if m else ""

    name = seg("이름")
    if not name:
        return None
    return {"name": name, "description": seg("설명"), "template": seg("답변")}
