"""로컬 RAG (B2).

내 문서 폴더(txt/md/pdf)를 색인하고 키워드/TF 기반으로 관련 구절을
검색한다. 순수 함수 위주 (테스트 가능).

  * load_documents(dir) — 지원 확장자 문서 로드
  * tokenize(text)
  * search(query, docs) — 단어 중첩 점수 기반 상위 구절
  * context_for(query, docs) — LLM에 넘길 컨텍스트
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

SUPPORTED_SUFFIXES = (".txt", ".md", ".markdown")


def tokenize(text: str) -> list[str]:
    return [t for t in re.split(r"[^\w가-힣]+", str(text or "").lower()) if len(t) >= 2]


def load_documents(directory: str | Path, max_files: int = 100) -> list[dict[str, Any]]:
    """지원 문서 로드. [{path, text}] 반환."""
    docs: list[dict[str, Any]] = []
    root = Path(directory)
    if not root.exists():
        return docs
    for f in root.rglob("*"):
        if len(docs) >= max_files:
            break
        if f.suffix.lower() not in SUPPORTED_SUFFIXES:
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if text.strip():
            docs.append({"path": str(f), "text": text})
    return docs


def search(query: str, docs: list[dict[str, Any]], top_k: int = 5) -> list[dict[str, Any]]:
    """쿼리 단어가 문서에 부분 포함(한국어 조사 대응)되는 상위 구절 검색."""
    q_tokens = tokenize(query)
    if not q_tokens:
        return []
    scored: list[tuple[float, dict[str, Any]]] = []
    for doc in docs:
        lowered = str(doc["text"]).lower()
        overlap = sum(1 for t in q_tokens if t in lowered)
        if overlap == 0:
            continue
        snippet = _snippet(doc["text"], set(q_tokens))
        score = overlap + min(1.0, overlap / max(1, len(tokenize(doc["text"])))) * 2
        scored.append((score, {"path": doc["path"], "snippet": snippet, "overlap": overlap}))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [s for _, s in scored[:top_k]]


def _snippet(text: str, q_tokens: set[str], width: int = 200) -> str:
    words = tokenize(text)
    for i, w in enumerate(words):
        if w in q_tokens:
            start = max(0, i - 5)
            return " ".join(words[start : start + 30])[:width]
    return text[:width]


def context_for(query: str, docs: list[dict[str, Any]], top_k: int = 5) -> str:
    """LLM 프롬프트용 컨텍스트 문자열."""
    results = search(query, docs, top_k=top_k)
    if not results:
        return ""
    lines = [f"문서 검색 결과 ({query}):"]
    for i, r in enumerate(results, 1):
        lines.append(f"[{i}] {r['path']}: {r['snippet']}")
    return "\n".join(lines)


def extract_rag_command(text: str) -> str | None:
    """'내 문서에서 OO 찾아줘' → 쿼리 추출."""
    m = re.search(r"(?:내\s*)?(?:문서|파일|자료)(?:에서|에서)?\s*(.+?)(?:찾아|검색)(?:줘|해줘)?", str(text or ""))
    if m:
        q = m.group(1).strip()
        if 1 <= len(q) <= 60:
            return q
    return None
