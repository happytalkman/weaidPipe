"""문서 자동 생성 — 누적 대화 그래프 기반 보고서.

누적 온톨로지(턴/주제/트리플/인과/요약)를 종합해 마크다운 보고서를
생성한다. LLM(원콜) 우선, 실패 시 결정적 로컬 템플릿.

테스트 가능한 순수 함수:
  * build_report_context(store)
  * parse_report(data)
  * local_report(store, title)
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
EXPORT_DIR = BASE_DIR / "exports"

_SYSTEM = (
    "You are the WEAID report writer. Given the accumulated conversation "
    "knowledge graph, produce a structured analysis report. Respond with JSON "
    "only, no markdown. Shape: "
    '{"title":str,"summary":str,"key_findings":[str],"topics":[str],'
    '"causal_insights":[str],"recommendations":[str]}. All text in Korean.'
)


def build_report_context(store: dict[str, Any]) -> str:
    """그래프 → LLM 프롬프트용 컨텍스트."""
    parts = [
        f"턴 {len(store.get('turns', [])) // 2}회 · 주제 {len(store.get('topics', []))}개 · "
        f"트리플 {len(store.get('triples', []))}개",
        "",
        "주제: " + ", ".join(str(t.get('label', '')) for t in store.get("topics", [])[-20:]),
        "",
        "S-P-O 트리플:",
    ]
    for t in store.get("triples", [])[-40:]:
        parts.append(
            f"- [{t.get('layer', 'semantic')}] {t.get('subject')} → {t.get('predicate')} → {t.get('object')}"
        )
    causal = [r for r in store.get("relations", []) if r.get("kind") == "causal"]
    parts.append("")
    parts.append("인과관계:")
    for r in causal[-15:]:
        parts.append(f"- {r.get('source')} → {r.get('target')} ({r.get('label', '')})")
    summaries = store.get("summaries", [])
    if summaries:
        parts.append("")
        parts.append("대화 요약:")
        for s in summaries[-5:]:
            parts.append(f"- {s.get('summary', '')}")
    return "\n".join(parts)


def parse_report(data: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(data, dict):
        return None
    return {
        "title": str(data.get("title", "")).strip()[:120] or "대화 분석 보고서",
        "summary": str(data.get("summary", "")).strip()[:800],
        "key_findings": [str(x)[:300] for x in (data.get("key_findings") or [])][:8],
        "topics": [str(x)[:120] for x in (data.get("topics") or [])][:10],
        "causal_insights": [str(x)[:200] for x in (data.get("causal_insights") or [])][:8],
        "recommendations": [str(x)[:300] for x in (data.get("recommendations") or [])][:6],
    }


def local_report(store: dict[str, Any], title: str = "대화 분석 보고서") -> str:
    """결정적 로컬 폴백 보고서."""
    lines = [f"# {title}", "", f"생성: {datetime.now().strftime('%Y-%m-%d %H:%M')}", ""]
    lines.append("## 개요")
    lines.append(
        f"- 대화 {len(store.get('turns', [])) // 2}회 · 주제 {len(store.get('topics', []))}개 · "
        f"S-P-O 트리플 {len(store.get('triples', []))}개 · 인과관계 "
        f"{sum(1 for r in store.get('relations', []) if r.get('kind') == 'causal')}개"
    )
    lines.append("")
    lines.append("## 주요 주제")
    for t in store.get("topics", [])[-10:]:
        layer = t.get("layer", "semantic")
        lines.append(f"- [{layer}] {t.get('label')}")
    causal = [r for r in store.get("relations", []) if r.get("kind") == "causal"]
    if causal:
        lines.append("")
        lines.append("## 인과 인사이트")
        for r in causal[-8:]:
            lines.append(f"- {r.get('source')} → {r.get('target')}")
    summaries = store.get("summaries", [])
    if summaries:
        lines.append("")
        lines.append("## 대화 요약")
        for s in summaries[-3:]:
            lines.append(f"- {s.get('summary', '')}")
    lines.append("")
    lines.append("## 제언")
    lines.append("- 그래프 기반 후속 분석 또는 '왜?' 인과 추적으로 심화 탐색을 권장합니다.")
    return "\n".join(lines)


def report_to_markdown(report: dict[str, Any]) -> str:
    lines = [f"# {report['title']}", "", f"생성: {datetime.now().strftime('%Y-%m-%d %H:%M')}", ""]
    if report.get("summary"):
        lines += ["## 요약", report["summary"], ""]
    if report.get("key_findings"):
        lines += ["## 핵심 발견"] + [f"- {x}" for x in report["key_findings"]]
        lines.append("")
    if report.get("topics"):
        lines += ["## 주요 주제"] + [f"- {x}" for x in report["topics"]]
        lines.append("")
    if report.get("causal_insights"):
        lines += ["## 인과 인사이트"] + [f"- {x}" for x in report["causal_insights"]]
        lines.append("")
    if report.get("recommendations"):
        lines += ["## 제언"] + [f"- {x}" for x in report["recommendations"]]
    return "\n".join(lines)


def generate_report(store: dict[str, Any], timeout_s: float = 45.0) -> dict[str, Any] | None:
    try:
        from core.llm import generate_json

        data = generate_json(_SYSTEM, build_report_context(store), timeout_s=timeout_s)
        return parse_report(data)
    except Exception:
        return None


def write_report(markdown: str, path: Path | None = None) -> Path:
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = path or EXPORT_DIR / f"report_{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
    out.write_text(markdown, encoding="utf-8")
    return out


def extract_report_command(text: str) -> bool:
    t = str(text or "")
    return bool(re.search(r"보고서|레포트|리포트|분석\s*문서", t)) and bool(re.search(r"작성|만들|생성|뽑아", t))
