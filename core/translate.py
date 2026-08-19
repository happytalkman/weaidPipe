"""실시간 통역 (STT→번역→TTS 체인).

마지막 사용자 발화(이미 STT 완료된 전사)를 대상 언어로 번역한다.
순수 함수: extract_translate_command, language_name, translate_text(주입 가능).
"""
from __future__ import annotations

import re
from typing import Callable

LANGUAGES = {
    "영어": "English", "영어로": "English", "english": "English",
    "일본어": "Japanese", "일본어로": "Japanese", "japanese": "Japanese",
    "중국어": "Chinese", "중국어로": "Chinese", "chinese": "Chinese",
    "한국어": "Korean", "한국어로": "Korean", "korean": "Korean",
    "스페인어": "Spanish", "프랑스어": "French", "독일어": "German",
}


def language_name(lang: str) -> str | None:
    key = str(lang or "").strip().lower()
    return LANGUAGES.get(key)


def extract_translate_command(text: str) -> str | None:
    """'영어로 통역해줘' / '일본어로 번역해줘' → 대상 언어명(영문)."""
    t = str(text or "")
    if not re.search(r"통역|번역", t):
        return None
    m = re.search(r"([가-힣a-zA-Z]+)로\s*(?:통역|번역)", t)
    if m:
        lang = language_name(m.group(1))
        if lang:
            return lang
    # '영어 통역' 형태
    m = re.search(r"([가-힣a-zA-Z]+)\s*(?:통역|번역)", t)
    if m:
        lang = language_name(m.group(1))
        if lang:
            return lang
    return None


def translate_text(text: str, target_lang: str, generate_fn: Callable | None = None) -> str:
    """LLM 번역 (generate_fn 주입 가능)."""
    from core.llm import generate as _default

    gen = generate_fn or _default
    prompt = (
        f"다음 문장을 {target_lang}로 자연스럽게 번역하세요. 번역문만 출력하세요.\n\n"
        f"원문: {text}"
    )
    return gen("당신은 전문 통역사입니다.", prompt).strip()


def translate_chain(text: str, target_lang: str, generate_fn: Callable | None = None) -> dict[str, str]:
    """STT(완료된 전사)→번역 체인의 순수 래퍼 (결과 구조 반환)."""
    translated = translate_text(text, target_lang, generate_fn=generate_fn)
    return {"source": str(text or ""), "target_lang": target_lang, "translated": translated}
