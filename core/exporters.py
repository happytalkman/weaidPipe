"""Obsidian / Notion 내보내기 (D3).

마크다운 콘텐츠를 Obsidian(프론트매터+위키링크) / Notion(마크다운) 형식으로
변환한다. 순수 함수 — 포맷 검증 테스트 가능.

환경변수 OBSIDIAN_VAULT_PATH가 있으면 자동 동기화.
"""
from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

WIKILINK_RE = re.compile(r"\[\[([^\]|]+)\]\]")


def to_obsidian(markdown: str, title: str, tags: list[str] | None = None) -> str:
    """Obsidian 노트 변환: YAML 프론트매터 + 위키링크 정규화."""
    tags = tags or []
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    fm = ["---", f"title: \"{title}\"", f"date: {now}", f"tags: [{', '.join(tags)}]", "---", ""]
    body = WIKILINK_RE.sub(r"[[\1]]", markdown)
    return "\n".join(fm) + "\n" + body


def to_notion(markdown: str) -> str:
    """Notion 붙여넣기 호환 마크다운 (위키링크 → 일반 텍스트, 태스크 정규화)."""
    text = WIKILINK_RE.sub(r"\1", markdown)
    text = re.sub(r"^- \[ \]", "- [ ]", text, flags=re.MULTILINE)
    return text


def sync_to_obsidian(
    markdown: str,
    title: str,
    tags: list[str] | None = None,
    vault_path: str | None = None,
) -> Path | None:
    """OBSIDIAN_VAULT_PATH가 설정되어 있으면 노트 파일로 저장."""
    vault = vault_path or os.environ.get("OBSIDIAN_VAULT_PATH", "").strip()
    if not vault:
        return None
    out = Path(vault) / "WEAID" / f"{_slug(title)}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(to_obsidian(markdown, title, tags), encoding="utf-8")
    return out


def _slug(title: str) -> str:
    slug = re.sub(r"[^\w가-힣-]+", "-", str(title or "note")).strip("-")
    return slug[:80] or "note"


def export_bundle(markdown: str, title: str, tags: list[str] | None = None) -> dict[str, Any]:
    """모든 포맷 결과 묶음 (테스트/통합용)."""
    obs = to_obsidian(markdown, title, tags)
    notion = to_notion(markdown)
    return {"obsidian": obs, "notion": notion, "synced": None}
