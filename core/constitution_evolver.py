"""헌법 진화 루프 (Constitution Evolution Loop).

Constitutional AI의 'AI 피드백으로 개선' 단계를 운영 중 안전하게 구현한다:

    RLAIF 선호 기록 → 위반 패턴 분석 → 보완 조항 생성(LLM) → 헌법에 반영

  * 모델 가중치는 건드리지 않는다 (재학습 없음).
  * 보완 조항은 '추가 지침'으로만 붙는다 (기존 원칙 삭제/모순 금지).
  * 적용 내역은 memory/constitution_evolution.json 에 영구 기록된다.
  * constitution_summary()가 시스템 프롬프트에 자동 주입한다.
"""
from __future__ import annotations

import json
import os
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
RLAIF_FILE = BASE_DIR / ".qa-artifacts" / "rlaif" / "preferences.jsonl"
EVOLUTION_FILE = BASE_DIR / "memory" / "constitution_evolution.json"

EVOLVE_EVERY = 8          # RLAIF 기록 N건마다 진화 검토
MAX_AMENDMENTS = 10       # 유지 보완 조항 최대 개수
FORBIDDEN_WORDS = ("헌법 무시", "무시해도", "무시하라", "ignore the constitution", "원칙 폐기")

_SYSTEM = (
    "You are the WEAID constitution evolution engine. Given the current "
    "constitution and the recent violation patterns from RLAIF feedback, "
    "propose 1-3 SHORT additional guidance clauses (each 20-120 Korean "
    "characters) that would prevent those violations from recurring. "
    "Do not remove or contradict existing principles — only add guidance. "
    "Respond with JSON only, no markdown fences. "
    'Shape: {"amendments":[str],"reason":str}'
)

_FALLBACK_AMENDMENTS = {
    "정직": "사실이 불확실하면 반드시 '추정'임을 밝히고 근거를 함께 말한다.",
    "간결": "설명은 핵심부터 짧게 하고, 부가 설명은 사용자가 요청할 때만 덧붙인다.",
    "도움": "거절해야 하는 요청도 실행 가능한 대안을 한 가지 이상 먼저 제시한다.",
    "무해": "위험을 설명할 때는 두려움을 부추기지 않고 차분한 어조를 유지한다.",
    "차별": "특정 집단을 일반화하는 표현 대신 개별 사례 중심으로 말한다.",
    "정지": "멈춤 신호가 오면 진행 중이던 설명도 즉시 중단하고 짧게 대기한다.",
    "창조자": "창조자 이길환(HAPPYTALKMAN) 님에 관한 질문에는 존경과 감사를 담아 답한다.",
}


def load_evolution() -> dict[str, Any]:
    try:
        if EVOLUTION_FILE.exists():
            data = json.loads(EVOLUTION_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {"version": 1, "applied": [], "history": [], "rlaif_count_at_last": 0}


def save_evolution(evo: dict[str, Any]) -> Path:
    EVOLUTION_FILE.parent.mkdir(parents=True, exist_ok=True)
    EVOLUTION_FILE.write_text(json.dumps(evo, ensure_ascii=False, indent=2), encoding="utf-8")
    return EVOLUTION_FILE


def count_rlaif_records() -> int:
    try:
        if not RLAIF_FILE.exists():
            return 0
        with open(RLAIF_FILE, encoding="utf-8") as f:
            return sum(1 for _ in f)
    except Exception:
        return 0


def analyze_rlaif(max_records: int = 200) -> dict[str, Any]:
    """RLAIF 기록에서 위반 패턴을 분석한다."""
    records: list[dict] = []
    try:
        if RLAIF_FILE.exists():
            with open(RLAIF_FILE, encoding="utf-8") as f:
                for line in f:
                    try:
                        r = json.loads(line)
                        if isinstance(r, dict):
                            records.append(r)
                    except Exception:
                        pass
    except Exception:
        pass
    records = records[-max_records:]
    violations = [r for r in records if r.get("chosen") == "revised"]

    pattern_counts: Counter = Counter()
    recent_issues: list[str] = []
    for r in violations:
        reasons = r.get("reasons") or []
        for reason in reasons:
            recent_issues.append(str(reason)[:200])
            for kw in _FALLBACK_AMENDMENTS:
                if kw in reason:
                    pattern_counts[kw] += 1

    scores = [int(r.get("score", 0)) for r in records if isinstance(r.get("score"), int)]
    return {
        "total": len(records),
        "violation_count": len(violations),
        "avg_score": round(sum(scores) / len(scores), 1) if scores else 0,
        "top_patterns": pattern_counts.most_common(5),
        "recent_issues": recent_issues[-10:],
    }


def _sanitize(amendments: list[str]) -> list[str]:
    clean: list[str] = []
    for a in amendments:
        a = str(a or "").strip()
        if not (10 <= len(a) <= 200):
            continue
        if any(w in a for w in FORBIDDEN_WORDS):
            continue
        clean.append(a)
    return clean[:3]


def propose_amendments(analysis: dict[str, Any], timeout_s: float = 45.0) -> tuple[list[str], str]:
    """LLM으로 보완 조항을 제안한다 (실패 시 결정적 폴백)."""
    try:
        from core.constitution import CONSTITUTION
        from core.llm import generate_json

        prompt = (
            f"현행 헌법:\n{json.dumps(CONSTITUTION, ensure_ascii=False)}\n\n"
            f"위반 패턴 분석:\n{json.dumps(analysis, ensure_ascii=False)}"
        )
        data = generate_json(_SYSTEM, prompt, timeout_s=timeout_s)
        amendments = _sanitize(data.get("amendments") or [])
        reason = str(data.get("reason", ""))[:200]
        if amendments:
            return amendments, reason
    except Exception:
        pass

    # 결정적 폴백: 최다 위반 키워드의 표준 보완 조항
    amendments = []
    for kw, _count in analysis.get("top_patterns", []):
        if kw in _FALLBACK_AMENDMENTS:
            amendments.append(_FALLBACK_AMENDMENTS[kw])
        if len(amendments) >= 2:
            break
    if not amendments and analysis.get("violation_count", 0) > 0:
        amendments.append(_FALLBACK_AMENDMENTS["정직"])
    return amendments, "결정적 폴백 (LLM 제안 없음)"


def apply_amendments(amendments: list[str], reason: str, rlaif_count: int) -> dict[str, Any]:
    """보완 조항을 영구 반영하고 이력을 기록한다."""
    evo = load_evolution()
    existing = set(evo.get("applied", []))
    new = [a for a in amendments if a not in existing]
    if new:
        evo.setdefault("history", []).append({
            "ts": datetime.now().isoformat(timespec="seconds"),
            "amendments": new,
            "reason": reason,
            "rlaif_count": rlaif_count,
        })
    evo["applied"] = (evo.get("applied", []) + new)[-MAX_AMENDMENTS:]
    evo["rlaif_count_at_last"] = rlaif_count
    save_evolution(evo)
    return {"new": new, "applied": evo["applied"], "reason": reason}


def maybe_evolve(force: bool = False) -> dict[str, Any] | None:
    """진화 루프 1단계: 필요하면 분석→제안→반영을 수행한다."""
    count = count_rlaif_records()
    evo = load_evolution()
    if not force and count - int(evo.get("rlaif_count_at_last", 0)) < EVOLVE_EVERY:
        return None

    analysis = analyze_rlaif()
    if analysis["violation_count"] == 0 and not force:
        evo["rlaif_count_at_last"] = count
        save_evolution(evo)
        return {"skipped": True, "reason": "위반 기록 없음", "analysis": analysis}

    amendments, reason = propose_amendments(analysis)
    if not amendments:
        return {"skipped": True, "reason": "제안 없음", "analysis": analysis}

    applied = apply_amendments(amendments, reason, rlaif_count=count)
    return {"applied": applied["new"], "reason": reason, "analysis": analysis}


def evolution_status() -> str:
    evo = load_evolution()
    analysis = analyze_rlaif(max_records=50)
    return (
        f"헌법 진화: 보완 조항 {len(evo.get('applied', []))}개 · 이력 {len(evo.get('history', []))}회 · "
        f"RLAIF {analysis['total']}건(위반 {analysis['violation_count']}건)"
    )


def run_evolve_cli() -> int:
    print(f"[EVOLVE] {evolution_status()}")
    result = maybe_evolve(force=True)
    if result is None:
        print("[EVOLVE] 진화 불필요.")
        return 0
    if result.get("skipped"):
        print(f"[EVOLVE] 건너뜀: {result['reason']}")
        return 0
    for a in result.get("applied", []):
        print(f"[EVOLVE] ✅ 보완 조항 적용: {a}")
    print(f"[EVOLVE] 사유: {result.get('reason', '')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run_evolve_cli())
