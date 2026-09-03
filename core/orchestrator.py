"""봇 오케스트레이션 파이프라인 (C2).

여러 봇을 순차로 연결해 워크플로를 자동 실행한다:
  리서처 → 요약 → 보고  (각 단계의 출력이 다음 단계 입력으로)

순수 함수: run_pipeline (generate_fn 주입 가능 — 테스트 용이)
"""
from __future__ import annotations

from typing import Any, Callable

from core import bots


def build_pipeline(
    bots_store: list[dict[str, Any]],
    stage_bots: list[str],
    input_text: str,
    generate_fn: Callable | None = None,
) -> list[dict[str, Any]]:
    """지정한 봇 순서로 파이프라인 실행.

    각 단계: 이전 출력(또는 입력)을 해당 봇에게 전달.
    반환: [{"stage": i, "bot": name, "output": text, "input": text}]
    """
    results: list[dict[str, Any]] = []
    current = input_text
    for i, bot_key in enumerate(stage_bots):
        bot = bots.get_bot(bots_store, bot_key)
        if bot is None:
            results.append({"stage": i, "bot": bot_key, "input": current, "output": "", "error": "bot not found"})
            break
        prompt = (
            f"[파이프라인 단계 {i + 1}/{len(stage_bots)}]\n"
            f"입력:\n{current}\n\n"
            f"당신의 역할({bot.get('name')})에 맞게 처리하고, 다음 단계가 이어받을 수 있는 "
            f"형식으로 출력하세요."
        )
        output = bots.bot_reply(bot, prompt, generate_fn=generate_fn)
        results.append({"stage": i, "bot": bot.get("name"), "input": current, "output": output})
        current = output
    return results


def pipeline_summary(results: list[dict[str, Any]]) -> str:
    """파이프라인 결과 요약 (로그 표시용)."""
    lines = [f"🔄 파이프라인 완료 ({len(results)}단계):"]
    for r in results:
        if r.get("error"):
            lines.append(f"  {r['stage'] + 1}. {r['bot']} — ❌ {r['error']}")
        else:
            out = str(r.get("output", ""))[:80]
            lines.append(f"  {r['stage'] + 1}. {r['bot']} → {out}")
    return "\n".join(lines)


_PIPELINE_WORDS = ("파이프라인", "순서대로 처리", "워크플로", "릴레이")


def is_pipeline_command(text: str) -> bool:
    t = str(text or "")
    return any(w in t for w in _PIPELINE_WORDS) and "봇" in t
