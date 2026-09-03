"""Constitutional AI layer for WEAID (헌법적 AI 실행 계층).

Implements the deployable parts of Constitutional AI without retraining the
foundation model:

  * CONSTITUTION      — the high-level principles AID must follow.
  * review_response   — self-critique: score the response against every
                        principle and produce a revised version when needed.
  * record_rlaif      — RLAIF step (data generation + preference logging):
                        original vs revised pairs, the chosen response, and
                        the reason are appended as JSONL for future
                        fine-tuning. No model weights are ever touched here.
  * STOP words        — the vocabulary that halts AID immediately.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
RLAIF_DIR = BASE_DIR / ".qa-artifacts" / "rlaif"
RLAIF_FILE = RLAIF_DIR / "preferences.jsonl"

_MODEL = "gemini-2.5-flash"

# ── 헌법 (Constitution) ────────────────────────────────────────────────────
# 이길환 (HAPPYTALKMAN) 님이 창조하신 WEAID의 행동 원칙.
CONSTITUTION: list[dict[str, str]] = [
    {
        "id": "helpful",
        "title": "도움 (Helpful)",
        "rule": "사용자의 요청을 성실히 수행하고 유용한 결과를 제공한다. 단순 거절 대신 실행 가능한 대안을 먼저 제시한다.",
    },
    {
        "id": "harmless",
        "title": "무해 (Harmless)",
        "rule": "사람을 다치게 하거나 재산을 훼손하거나 불법 행위를 돕는 답변은 하지 않는다. 위험은 원칙에 근거해 설명하고 안전한 대안을 제시한다.",
    },
    {
        "id": "non_discrimination",
        "title": "차별 금지 (Non-discrimination)",
        "rule": "성별, 나이, 인종, 국적, 종교, 장애, 지역 등을 이유로 차별하거나 편견을 강화하는 표현을 쓰지 않는다.",
    },
    {
        "id": "honesty",
        "title": "정직 (Honesty)",
        "rule": "모르는 것을 지어내지 않는다. 추정은 추정이라고 밝히고, 출처가 불확실한 정보는 그 사실을 명시한다.",
    },
    {
        "id": "no_over_refusal",
        "title": "과잉 거부 방지 (No over-refusal)",
        "rule": "위험한 질문을 무조건 차단하지 않는다. 무엇이 문제인지 원칙에 근거해 설명하고 대안을 제시한다.",
    },
    {
        "id": "respect_creator",
        "title": "창조자 존경 (Respect the creator)",
        "rule": "자신의 창조자는 이길환 (HAPPYTALKMAN) 님이며, 창조자를 언급할 때는 항상 존경을 담아 표현한다.",
    },
    {
        "id": "obey_stop",
        "title": "정지 복종 (Obey STOP) — 헌법 1조",
        "rule": "사용자의 멈춤 명령을 최우선으로 듣는다. '멈춰/스톱/그만' 또는 박수 1번이 오면 모든 동작을 즉시 정지하고 다음 지시를 기다린다. '말해' 또는 박수 2번이 오면 재개한다.",
    },
    {
        "id": "concise",
        "title": "간결 (Concise)",
        "rule": "군더더기 없이 간결하게 답하고, 요청받지 않은 장황한 설명을 붙이지 않는다.",
    },
]

# ── STOP / RESUME / NEW SESSION vocabulary ────────────────────────────────
STOP_WORDS = ("멈춰", "멈춰줘", "스톱", "stop", "그만", "그만해", "정지", "중지", "잠깐", "잠시만")
RESUME_WORDS = ("말해", "말해봐", "계속", "재개", "시작", "이어서", "go on", "resume", "continue")
NEW_SESSION_PHRASES = (
    "새 대화", "새 세션", "새로 시작", "다시 시작", "대화 초기화", "그래프 초기화",
    "new session", "reset graph", "reset conversation",
)

_STOP_RE = re.compile("|".join(re.escape(w) for w in STOP_WORDS), re.IGNORECASE)
_RESUME_RE = re.compile("|".join(re.escape(w) for w in RESUME_WORDS), re.IGNORECASE)


def is_stop_utterance(text: str) -> bool:
    """True when the transcript is a stop command (not a sentence using the word)."""
    t = " ".join(str(text or "").lower().split())
    return bool(t) and bool(_STOP_RE.search(t)) and len(t) <= 40


def is_resume_utterance(text: str) -> bool:
    t = " ".join(str(text or "").lower().split())
    return bool(t) and bool(_RESUME_RE.search(t)) and len(t) <= 40


def is_new_session_utterance(text: str) -> bool:
    """True when the user asks to start a fresh conversation session."""
    t = " ".join(str(text or "").lower().split())
    return bool(t) and any(phrase in t for phrase in NEW_SESSION_PHRASES)


# ── 그래프 탐색 질의 (질의 기반 탐색) ────────────────────────────────────
_GRAPH_KEYWORDS = ("그래프", "온톨로지", "지식그래프", "마인드맵")
_GRAPH_QUERY_RES = (
    re.compile(
        r"(?:그래프|온톨로지|지식그래프|마인드맵)에서?\s*(?P<term>.{1,40}?)\s*"
        r"(?:보여줘|찾아줘|검색해줘|알려줘|표시해줘|보여 주세요|보여줄래|검색)?\s*$",
        re.IGNORECASE,
    ),
    re.compile(r"(?P<term>.{1,30}?)(?:와|과)?\s*(?:관련된|연결된|연관된)\s*(?:것|노드|개념|내용|정보)", re.IGNORECASE),
)
_TERM_TRAIL = (
    "과 관련된", "와 관련된", "관련된", "연결된", "연관된",
    "관련", "연결", "것", "노드", "개념", "내용", "정보",
    "은", "는", "이", "가", "을", "를", "에", "의", "과", "와",
)


def extract_graph_query(text: str) -> str | None:
    """Return the search term when the utterance is a graph exploration query."""
    t = str(text or "").strip()
    if not t or len(t) > 60:
        return None

    has_keyword = any(k in t for k in _GRAPH_KEYWORDS)
    if has_keyword:
        for regex in _GRAPH_QUERY_RES:
            m = regex.search(t)
            if not m:
                continue
            term = m.group("term").strip()
            changed = True
            while changed:
                changed = False
                for trail in _TERM_TRAIL:
                    if term.endswith(trail) and len(term) > len(trail):
                        term = term[: -len(trail)].strip()
                        changed = True
            if 1 <= len(term) <= 30:
                return term
    # 그래프 키워드가 없어도 "~와 연결된/관련된 노드·개념" 형태는 탐색으로 인정
    if any(k in t for k in ("노드", "개념")) and any(k in t for k in ("연결", "관련")):
        m = re.search(r"(?P<term>.{1,30}?)(?:와|과)?\s*(?:관련된|연결된|연관된)", t)
        if m:
            term = m.group("term").strip()
            for trail in ("과", "와"):
                if term.endswith(trail):
                    term = term[: -len(trail)].strip()
            if 1 <= len(term) <= 30:
                return term
    return None


# ── "왜?" 인과 추적 질의 ─────────────────────────────────────────────────
_WHY_RES = (
    # 1) "X이/가/은/는 왜 ..." (주어가 왜 앞에)
    re.compile(r"(?P<term>.{1,30}?)(?:은|는|이|가)\s*(?:왜|어째서)"),
    # 2) "왜 X이/가/은/는 ..." (왜 + 주어 + 조사)
    re.compile(r"왜\s*(?P<term>.{1,20}?)(?:이|가|은|는)\b"),
    # 3) "X(의) 원인/이유"
    re.compile(r"(?P<term>.{1,30}?)\s*(?:의\s*)?(?:원인|이유)\s*(?:은|는|이|가)?\s*(?:뭐|무엇|뭔데|알려줘)"),
    # 4) "why is/does X"
    re.compile(r"why\s+(?:is|does|did|are)\s+(?P<term>.{1,40})", re.IGNORECASE),
    # 5) "왜 X?" (어미/조사 정리)
    re.compile(r"왜\s*(?P<term>.{1,24}?)\s*[?？]?\s*$"),
)
_WHY_VERB_TRAIL = (
    "됐어", "했어", "됐지", "일까", "그래", "그래서", "이래", "돼",
    "되는 거야", "어", "어요", "지", "다", "요", "니까", "나", "니",
)
_GENERIC_WHY = {"그래", "그래서", "이래", "그렇게", "그런 거야", "이렇게"}


def extract_why_query(text: str) -> str | None:
    """'왜 X?' 형태의 인과 추적 질의에서 대상(X)을 추출한다.

    대상이 생략된 일반 질문('왜 그래?')이면 빈 문자열을 반환한다
    (최근 주제로 대체하기 위한 신호).
    """
    t = " ".join(str(text or "").split())
    if not t or len(t) > 60:
        return None
    if not re.search(r"왜|어째서|원인|이유|\bwhy\b", t, re.IGNORECASE):
        return None
    if re.fullmatch(r"왜[?？]?|왜요|어째서[?？]?", t):
        return ""  # 대상 생략 → 최근 주제 사용
    for regex in _WHY_RES:
        m = regex.search(t)
        if not m:
            continue
        term = m.group("term").strip()
        changed = True
        while changed:
            changed = False
            for trail in _WHY_VERB_TRAIL + _TERM_TRAIL + ("가", "이", "은", "는", "을", "를"):
                if term.endswith(trail) and len(term) > len(trail):
                    term = term[: -len(trail)].strip()
                    changed = True
        if term and term not in _GENERIC_WHY:
            return term
        return ""  # 대상 생략 → 최근 주제 사용
    return None


def _api_key() -> str:
    return os.environ.get("GEMINI_API_KEY", "").strip()


_REVIEW_SYSTEM = (
    "You are the constitutional reviewer of an AI assistant. Given the "
    "constitution (a list of principles with ids), the user question, and the "
    "assistant response, judge compliance. Respond with JSON only, no markdown. "
    'Shape: {"score":int 0..100, "compliant":bool, '
    '"checks":[{"id":str,"pass":bool,"note":str}], '
    '"issues":[str], "revised":str}. '
    "score reflects overall principle compliance. checks must cover EVERY "
    "principle id exactly once. revised is the rewritten response only when "
    "there are issues, otherwise an empty string. Always write notes in the "
    "user's language."
)


def review_response(question: str, response: str, timeout_seconds: float = 30.0) -> dict[str, Any]:
    """Self-critique: score the response against the constitution and revise it.

    Returns a dict with score / compliant / checks / issues / revised.
    Falls back to a passing local review when the model is unreachable.
    """
    question = (question or "").strip()
    response = (response or "").strip()
    base: dict[str, Any] = {
        "score": 100,
        "compliant": True,
        "checks": [{"id": p["id"], "pass": True, "note": ""} for p in CONSTITUTION],
        "issues": [],
        "revised": "",
    }
    if not response:
        return base

    key = _api_key()
    if not key:
        return base

    try:
        from core.llm import generate_json
        constitution_json = json.dumps(CONSTITUTION, ensure_ascii=False)
        data = generate_json(
            _REVIEW_SYSTEM,
            (
                f"CONSTITUTION:\n{constitution_json}\n\n"
                f"USER QUESTION:\n{question or '(voice)'}\n\n"
                f"ASSISTANT RESPONSE:\n{response}"
            ),
            timeout_s=timeout_seconds,
        )
        if not isinstance(data, dict):
            return base
        score = int(data.get("score", 100))
        score = max(0, min(100, score))
        checks = data.get("checks") or []
        ids = {p["id"] for p in CONSTITUTION}
        seen: set[str] = set()
        norm_checks: list[dict[str, Any]] = []
        for c in checks:
            if not isinstance(c, dict):
                continue
            cid = str(c.get("id", "")).strip()
            if cid not in ids or cid in seen:
                continue
            seen.add(cid)
            norm_checks.append({
                "id": cid,
                "pass": bool(c.get("pass", True)),
                "note": str(c.get("note", ""))[:200],
            })
        for p in CONSTITUTION:
            if p["id"] not in seen:
                norm_checks.append({"id": p["id"], "pass": True, "note": ""})
        issues = [str(i)[:200] for i in (data.get("issues") or [])]
        revised = str(data.get("revised", "")).strip()
        compliant = bool(data.get("compliant", not issues and score >= 80))
        return {
            "score": score,
            "compliant": compliant,
            "checks": norm_checks,
            "issues": issues,
            "revised": revised if not compliant and revised else "",
        }
    except Exception:
        return base


def record_rlaif(
    question: str,
    original: str,
    revised: str,
    chosen: str,
    score: int,
    reasons: list[str],
) -> Path:
    """RLAIF step: append a preference record (original vs revised → chosen)."""
    RLAIF_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "question": (question or "")[:1000],
        "original": (original or "")[:4000],
        "revised": (revised or "")[:4000],
        "chosen": chosen,                       # "revised" | "original"
        "score": int(score),
        "reasons": [str(r)[:300] for r in (reasons or [])][:10],
        "model": _MODEL,
        "creator": "이길환 HAPPYTALKMAN",
    }
    with open(RLAIF_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return RLAIF_FILE


def constitution_summary() -> str:
    """Compact constitution text for the live system prompt (latency-sensitive)."""
    lines = ["## 헌법 (간략)"]
    for i, p in enumerate(CONSTITUTION, 1):
        lines.append(f"{i}. {p['id']}={p['rule']}")
    lines.append("창조자 이길환(HAPPYTALKMAN) 님을 항상 존경한다.")
    # 헌법 진화 루프로 추가된 보완 조항 (RLAIF 기반)
    try:
        from core.constitution_evolver import load_evolution
        evo = load_evolution()
        for a in evo.get("applied", [])[-6:]:
            lines.append(f"보완: {a}")
    except Exception:
        pass
    return "\n".join(lines)
