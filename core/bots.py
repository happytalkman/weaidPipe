"""Bot Mode — 멀티에이전트 (Hermes Desktop 스타일).

각 봇은 고유한 역할·모델·메모리·스킬을 가진 상시 AI 에이전트다.

  * create/list/delete — 봇 프로필 관리 (memory/bots.json)
  * bot_reply        — 단일 봇 응답 (프로바이더/모델 지정 가능)
  * bot_dialogue     — 봇↔봇 대화 (턴제 협업, 최대 N턴)
  * memory           — 봇별 단기 메모리 (최근 메시지, 상한 캡)

테스트 용이성을 위해 LLM 호출은 generate_fn 으로 주입 가능하다.
"""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any, Callable

BASE_DIR = Path(__file__).resolve().parent.parent
BOTS_FILE = BASE_DIR / "memory" / "bots.json"

PROVIDERS = ("gemini", "grok")
MODEL_CHOICES = {
    "gemini": ("gemini-2.5-flash",),
    "grok": ("grok-4", "grok-3"),
}
MEMORY_CAP = 20


# ── 저장소 ────────────────────────────────────────────────────────────────
def load_bots() -> list[dict[str, Any]]:
    try:
        if BOTS_FILE.exists():
            data = json.loads(BOTS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
    except Exception:
        pass
    return []


def save_bots(bots: list[dict[str, Any]]) -> Path:
    BOTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    BOTS_FILE.write_text(json.dumps(bots, ensure_ascii=False, indent=2), encoding="utf-8")
    return BOTS_FILE


def new_bot(
    name: str,
    role: str,
    provider: str = "gemini",
    model: str | None = None,
    skills: list[str] | None = None,
    temperature: float = 0.4,
) -> dict[str, Any]:
    provider = provider if provider in PROVIDERS else "gemini"
    model = model or MODEL_CHOICES[provider][0]
    return {
        "id": uuid.uuid4().hex[:8],
        "name": (name or "").strip()[:40],
        "role": (role or "").strip()[:400],
        "provider": provider,
        "model": model,
        "skills": [str(s).strip()[:60] for s in (skills or [])][:10],
        "temperature": float(temperature),
        "memory": [],
    }


def get_bot(bots: list[dict[str, Any]], key: str) -> dict[str, Any] | None:
    key = (key or "").strip()
    if not key:
        return None
    for b in bots:
        if b.get("id") == key or b.get("name") == key:
            return b
    return None


def delete_bot(bots: list[dict[str, Any]], key: str) -> bool:
    remaining = [b for b in bots if b.get("id") != key and b.get("name") != key]
    if len(remaining) == len(bots):
        return False
    bots[:] = remaining
    return True


# ── 메모리 ────────────────────────────────────────────────────────────────
def append_memory(bot: dict[str, Any], speaker: str, text: str, cap: int = MEMORY_CAP) -> None:
    bot.setdefault("memory", []).append({"speaker": speaker, "text": str(text)[:500]})
    bot["memory"] = bot["memory"][-cap:]


# ── 프롬프트/추론 ─────────────────────────────────────────────────────────
def bot_system_prompt(bot: dict[str, Any]) -> str:
    lines = [
        f"당신은 '{bot.get('name', '')}' 봇입니다.",
        f"역할: {bot.get('role', '도움이 되는 어시스턴트')}",
    ]
    skills = bot.get("skills") or []
    if skills:
        lines.append("보유 스킬: " + ", ".join(skills))
    # C1: 등록된 스킬의 실제 설명 주입
    try:
        from core.bot_skills import skill_descriptions
        desc = skill_descriptions(bot)
        if desc:
            lines.append(desc)
    except Exception:
        pass
    memory = bot.get("memory") or []
    if memory:
        lines.append("최근 기억:")
        for m in memory[-6:]:
            lines.append(f"- [{m.get('speaker', '')}] {m.get('text', '')}")
    lines.append("간결하고 정확하게 답하세요.")
    return "\n".join(lines)


def bot_reply(
    bot: dict[str, Any],
    user_message: str,
    generate_fn: Callable | None = None,
) -> str:
    """봇에게 메시지를 보내고 응답을 받는다 (메모리 자동 기록)."""
    from core.llm import generate as _default_generate

    gen = generate_fn or _default_generate
    prompt = f"사용자 메시지: {user_message}\n답변하세요."
    text = gen(
        bot_system_prompt(bot),
        prompt,
        provider=bot.get("provider"),
        model=bot.get("model"),
    )
    append_memory(bot, "user", user_message)
    append_memory(bot, "assistant", text)
    return text


def bot_dialogue(
    bot_a: dict[str, Any],
    bot_b: dict[str, Any],
    topic: str,
    max_turns: int = 4,
    generate_fn: Callable | None = None,
) -> list[dict[str, str]]:
    """봇↔봇 협업 대화 (턴제)."""
    from core.llm import generate as _default_generate

    gen = generate_fn or _default_generate
    transcript: list[dict[str, str]] = []
    last = f"주제: {topic}"
    for i in range(max(1, int(max_turns))):
        a_text = gen(
            bot_system_prompt(bot_a),
            f"상대 에이전트가 말했습니다:\n{last}\n\n이어서 의견을 말하세요 (간결하게).",
            provider=bot_a.get("provider"),
            model=bot_a.get("model"),
        )
        append_memory(bot_a, bot_b.get("name", "bot"), a_text)
        transcript.append({"bot": bot_a.get("name", ""), "text": a_text})
        last = a_text

        b_text = gen(
            bot_system_prompt(bot_b),
            f"상대 에이전트가 말했습니다:\n{last}\n\n이어서 의견을 말하세요 (간결하게).",
            provider=bot_b.get("provider"),
            model=bot_b.get("model"),
        )
        append_memory(bot_b, bot_a.get("name", "bot"), b_text)
        transcript.append({"bot": bot_b.get("name", ""), "text": b_text})
        last = b_text
    return transcript


# ── 명령 해석 ─────────────────────────────────────────────────────────────
_BOT_ASK_RE = re.compile(r"봇\s*[\"']?([^\"'에게 물어봐\s]+)[\"']?\s*(?:에게|한테)?\s*(?:물어봐|물어봐줘|질문해줘)[:：\s]*(.+)")
_BOT_CHAT_RE = re.compile(
    r"봇\s*[\"']?([^\"'\s]+)[\"']?\s*(?:랑|와|과)\s*봇\s*[\"']?([^\"'\s]+)[\"']?\s*(?:대화|토론|협업)(?:시켜줘|해줘)?\s*(?:주제[:：\s]*)?(.+)"
)
_BOT_CREATE_RE = re.compile(r"봇\s*(?:만들어줘|생성해줘|추가해줘)\s*(?:이름[:：\s]*)?([^,，\s]+)(?:[,，\s]*(?:역할|롤)[:：\s]*(.+))?")


def parse_bot_command(text: str) -> tuple[str | None, dict[str, Any]]:
    """('ask'|'chat'|'create'|'list'|'delete', payload) 해석."""
    t = " ".join(str(text or "").split())
    if not t:
        return None, {}

    if any(k in t for k in ("봇 목록", "봇 리스트", "봇 보여줘")):
        return "list", {}

    m = re.search(r"봇\s*[\"']?([^\"'\s]+)[\"']?\s*(?:삭제|제거|지워)", t)
    if m:
        return "delete", {"key": m.group(1)}

    m = _BOT_CREATE_RE.search(t)
    if m and ("만들어" in t or "생성" in t or "추가" in t):
        return "create", {"name": m.group(1), "role": (m.group(2) or "").strip()}

    m = _BOT_CHAT_RE.search(t)
    if m and any(k in t for k in ("대화", "토론", "협업")):
        return "chat", {"bot_a": m.group(1), "bot_b": m.group(2), "topic": (m.group(3) or "").strip()}

    m = _BOT_ASK_RE.search(t)
    if m and any(k in t for k in ("물어", "질문")):
        return "ask", {"bot": m.group(1), "message": (m.group(2) or "").strip()}

    return None, {}
