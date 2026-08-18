"""자동 회의록 작성.

현재 세션의 대화 턴을 모아 회의록(안건/논의/결정/액션아이템/다음회의)을
생성한다. LLM(원콜, Gemini→Grok 폴백) 우선, 실패 시 결정적 로컬 템플릿.

테스트 가능한 순수 함수:
  * build_minutes_prompt(turns, speakers)
  * parse_minutes(data)
  * local_minutes(turns, title)  — 결정적 폴백
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
EXPORT_DIR = BASE_DIR / "exports"

_SYSTEM = (
    "You are the WEAID meeting-minutes writer. Given the conversation turns, "
    "produce structured meeting minutes. Respond with JSON only, no markdown. "
    'Shape: {"title":str,"attendees":[str],"agenda":[str],"discussion":[str],'
    '"decisions":[str],"action_items":[{"owner":str,"task":str}],'
    '"next_meeting":str}. All text in Korean.'
)


def build_minutes_prompt(turns: list[dict[str, Any]], speakers: list[str] | None = None) -> str:
    """회의록 생성을 위한 LLM 프롬프트 구성."""
    lines = []
    for t in turns:
        who = "참석자" if t.get("speaker") == "user" else "AID"
        lines.append(f"{who}: {str(t.get('text', ''))[:300]}")
    speaker_line = ", ".join(speakers or ["참석자"]) or "참석자"
    return "회의 참석자: " + speaker_line + "\n\n대화:\n" + "\n".join(lines[-60:])


def parse_minutes(data: dict[str, Any]) -> dict[str, Any] | None:
    """LLM JSON 응답을 정규화한다."""
    if not isinstance(data, dict):
        return None
    title = str(data.get("title", "")).strip() or "회의록"
    return {
        "title": title[:120],
        "attendees": [str(a)[:60] for a in (data.get("attendees") or [])][:10],
        "agenda": [str(a)[:200] for a in (data.get("agenda") or [])][:12],
        "discussion": [str(d)[:300] for d in (data.get("discussion") or [])][:20],
        "decisions": [str(d)[:300] for d in (data.get("decisions") or [])][:12],
        "action_items": [
            {"owner": str(x.get("owner", "") or "")[:40], "task": str(x.get("task", ""))[:200]}
            for x in (data.get("action_items") or [])
            if isinstance(x, dict) and x.get("task")
        ][:12],
        "next_meeting": str(data.get("next_meeting", ""))[:200],
    }


def local_minutes(turns: list[dict[str, Any]], title: str = "회의록") -> str:
    """결정적 로컬 폴백 회의록 (LLM 없이)."""
    lines = [f"# {title}", "", f"일시: {datetime.now().strftime('%Y-%m-%d %H:%M')}", ""]
    lines.append("## 참석자")
    lines.append("- 사용자, AID")
    lines.append("")
    lines.append("## 논의 내용")
    for t in turns[-40:]:
        who = "사용자" if t.get("speaker") == "user" else "AID"
        lines.append(f"- **{who}**: {str(t.get('text', ''))[:200]}")
    lines.append("")
    lines.append("## 결정 사항")
    lines.append("- (대화에서 명시된 결정이 없으면 추후 확인)")
    lines.append("")
    lines.append("## 액션 아이템")
    lines.append("- [ ] 대화 내용 검토 및 후속 조치")
    return "\n".join(lines)


def generate_minutes(
    turns: list[dict[str, Any]],
    speakers: list[str] | None = None,
    timeout_s: float = 45.0,
) -> dict[str, Any] | None:
    """LLM 회의록 생성 (실패 시 None)."""
    try:
        from core.llm import generate_json

        data = generate_json(_SYSTEM, build_minutes_prompt(turns, speakers), timeout_s=timeout_s)
        return parse_minutes(data)
    except Exception:
        return None


def minutes_to_markdown(minutes: dict[str, Any]) -> str:
    """정규화된 회의록 dict → 마크다운."""
    lines = [f"# {minutes['title']}", "", f"일시: {datetime.now().strftime('%Y-%m-%d %H:%M')}", ""]
    if minutes.get("attendees"):
        lines.append("## 참석자")
        lines += [f"- {a}" for a in minutes["attendees"]]
        lines.append("")
    if minutes.get("agenda"):
        lines.append("## 안건")
        lines += [f"- {a}" for a in minutes["agenda"]]
        lines.append("")
    if minutes.get("discussion"):
        lines.append("## 논의 내용")
        lines += [f"- {d}" for d in minutes["discussion"]]
        lines.append("")
    if minutes.get("decisions"):
        lines.append("## 결정 사항")
        lines += [f"- {d}" for d in minutes["decisions"]]
        lines.append("")
    if minutes.get("action_items"):
        lines.append("## 액션 아이템")
        lines += [f"- [ ] {a['owner'] + ': ' if a['owner'] else ''}{a['task']}" for a in minutes["action_items"]]
        lines.append("")
    if minutes.get("next_meeting"):
        lines.append(f"## 다음 회의\n{minutes['next_meeting']}")
    return "\n".join(lines)


def write_minutes(markdown: str, path: Path | None = None) -> Path:
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = path or EXPORT_DIR / f"meeting_notes_{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
    out.write_text(markdown, encoding="utf-8")
    return out


def extract_minutes_command(text: str) -> bool:
    """'회의록' 요청인지 판정."""
    t = str(text or "")
    has_topic = bool(re.search(r"회의록|회의|미팅", t))
    has_action = bool(re.search(r"작성|만들|정리|요약", t))
    return has_topic and has_action
