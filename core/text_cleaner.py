"""AI 출력 텍스트 위생 처리 (watermarks-remover Layer A — 네이티브 구현).

AI 모델 출력에 섞여 있는 무형(invisible) 문자를 제거한다. 외부 서비스 없이
정규식으로 동작하며, WEAID가 생성·유통하는 자기 콘텐츠의 위생/프라이버시
목적으로만 사용한다 (타인 콘텐츠의 워터마크 제거는 헌법 원칙과 충돌).

제거 대상:
  * zero-width space / word joiner / BOM (\\u200b, \\u2060, \\ufeff)
  * bidi 제어 문자 (\\u202a-\\u202e, \\u2066-\\u2069) + LRM/RLM (\\u200e, \\u200f)
  * 태그 문자 블록 (U+E0000..U+E007F), soft hyphen, 변형 선택자 VS1-15
  * 이국적 공백(\\u2000-\\u200a, \\u202f, \\u205f, \\u3000) → 일반 공백

보존 대상 (의미가 있으므로):
  * ZWJ/ZWNJ (이모지·일부 언어에 필요), VS16(\\ufe0f, 이모지 표시)
"""
from __future__ import annotations

import re

_INVISIBLE_RE = re.compile(
    "[\u00ad\u034f\u061c\u180b-\u180f\u200b\u200e\u200f"
    "\u202a-\u202e\u2060-\u2064\u2066-\u206f\u2028\u2029"
    "\ufeff\ufdd0-\ufdef\ufe00-\ufe0e\U000E0000-\U000E007F]"
)
_EXOTIC_SPACES_RE = re.compile("[\u2000-\u200a\u202f\u205f\u3000]")


def clean_text(text: str) -> str:
    """Remove invisible marks and normalize exotic spaces. Safe on any string."""
    if not text:
        return text
    t = _INVISIBLE_RE.sub("", str(text))
    t = _EXOTIC_SPACES_RE.sub(" ", t)
    return t.strip()


def was_cleaned(original: str) -> bool:
    return clean_text(original) != (original or "")


def clean_copy(text: str) -> str:
    """클립보드/파일 저장용 정화 (공백 정규화까지 포함)."""
    t = clean_text(text)
    return re.sub(r"[ \t]+", " ", t)
