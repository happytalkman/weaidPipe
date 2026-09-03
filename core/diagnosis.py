"""자가진단 대시보드 데이터 공급기.

자가진화 엔진(.qa-artifacts/self-improve/reports)과 헌법 진화·RLAIF·누적
그래프 상태를 하나의 스냅샷으로 제공한다. 순수 함수 위주로 단위 테스트
가능하게 구성.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
REPORTS_DIR = BASE_DIR / ".qa-artifacts" / "self-improve" / "reports"


def load_reports(limit: int = 20) -> list[dict[str, Any]]:
    """최신 순 사이클 리포트 목록."""
    if not REPORTS_DIR.exists():
        return []
    files = sorted(REPORTS_DIR.glob("cycle-*.json"), reverse=True)[:limit]
    reports: list[dict[str, Any]] = []
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                reports.append(data)
        except Exception:
            continue
    return reports


def summarize_report(report: dict[str, Any]) -> dict[str, Any]:
    """리포트를 대시보드용 요약으로 축약."""
    issues = report.get("issues") or []
    fixed = report.get("fixed") or []
    evo = report.get("evolution") or {}
    applied = evo.get("applied") if isinstance(evo, dict) else None
    return {
        "stamp": report.get("stamp", ""),
        "apply": bool(report.get("apply", False)),
        "issue_count": len(issues),
        "critical": sum(1 for i in issues if i.get("severity") == "critical"),
        "fixed_count": len(fixed) + len(report.get("ai_results") or []),
        "evolution_applied": len(applied) if applied else 0,
        "verify_ok": all(v.get("ok", False) for v in (report.get("verify") or {}).values()),
    }


def diagnosis_status() -> dict[str, Any]:
    """자가진단 스냅샷 (그래프·헌법진화·RLAIF·엔진 리포트)."""
    from core import graph_store
    from core.constitution_evolver import evolution_status
    from core.constitution_evolver import analyze_rlaif, load_evolution

    store = graph_store.load()
    evo = load_evolution()
    rlaif = analyze_rlaif(max_records=50)
    reports = load_reports(limit=20)
    last = summarize_report(reports[0]) if reports else None
    return {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "graph": {
            "turns": len(store.get("turns", [])),
            "topics": len(store.get("topics", [])),
            "triples": len(store.get("triples", [])),
            "entities": len(store.get("entities", [])),
            "relations": len(store.get("relations", [])),
            "summaries": len(store.get("summaries", [])),
        },
        "constitution": {
            "amendments": evo.get("applied", []),
            "history_count": len(evo.get("history", [])),
            "status": evolution_status(),
        },
        "rlaif": rlaif,
        "engine": {
            "report_count": len(reports),
            "last_cycle": last,
            "recent": [summarize_report(r) for r in reports[:10]],
        },
    }
