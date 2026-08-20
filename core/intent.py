"""의도 분류기 (B1).

로컬 명령 체인이 일반 대화를 오탐하지 않도록, "이 발화가 명령일 가능성"을
먼저 판정한다. 규칙 기반(즉시, 무비용) 우선 — LLM 분류는 옵션.

순수 함수: is_likely_command, classify (테스트 가능)
"""
from __future__ import annotations

import re

# 모든 로컬 명령 핸들러의 트리거 키워드 (정규식)
_COMMAND_PATTERNS = (
    r"\b(?:멈춰|멈춰줘|스톱|stop|그만|정지|중지|잠깐)\b",
    r"\b(?:말해|계속|재개|시작)\b",
    r"새\s*대화|새\s*세션|대화\s*초기화|그래프\s*초기화",
    r"그래프|온톨로지|지식그래프|마인드맵",
    r"매일|매주.*요일|예약",
    r"홀로그램",
    r"내\s*이름은",
    r"\b봇\b",
    r"회의록|보고서|레포트",
    r"\d+\s*(?:분|시간|초).*(?:알려|타이머|알람)|타이머|알람",
    r"내\s*(?:문서|파일|자료).*(?:찾아|검색)",
    r"에이전트",
    r"왜|원인|이유",
    r"스킬",
    r"시세|주가|브리핑",
    r"통역|번역",
    r"루틴",
    r"영어로\s*바꿔|한국어로\s*바꿔",
    r"파이프라인|순서대로",
    r"평가\s*보여",
)
_COMMAND_RE = re.compile("|".join(_COMMAND_PATTERNS), re.IGNORECASE)


def is_likely_command(text: str) -> bool:
    """명령 키워드가 포함되어 있으면 True (로컬 체인 실행 대상)."""
    return bool(_COMMAND_RE.search(str(text or "")))


def classify(text: str) -> str:
    """'command' | 'chat' 분류 (규칙 기반)."""
    return "command" if is_likely_command(text) else "chat"


# 일반 대화로 오탐되기 쉬운 예문 (테스트/튜닝용)
CHAT_EXAMPLES = (
    "오늘 날씨 어때?",
    "인공지능이 뭔지 설명해줘",
    "요즘 무슨 노래가 좋아?",
    "점심 메뉴 추천해줘",
    "주말에 뭐 하면 좋을까?",
)


def tune_check(examples: list[str]) -> list[str]:
    """예문 중 명령으로 오탐되는 것 반환 (회귀 테스트용)."""
    return [e for e in examples if is_likely_command(e)]
