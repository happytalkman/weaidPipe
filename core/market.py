"""주식/코인 브리핑 (공개 API 연동, 키 불필요).

  * get_crypto_prices(symbols) — CoinGecko 공개 API
  * get_stock_price(symbol)   — Yahoo Finance 공개 차트 API
  * 순수 함수: normalize_symbol, parse_coingecko, parse_yahoo, format_briefing
    (네트워크 없이 픽스처로 테스트 가능)
"""
from __future__ import annotations

import re
from typing import Any

CRYPTO_NAMES = {
    "비트코인": "bitcoin", "이더리움": "ethereum", "이더": "ethereum",
    "도지": "dogecoin", "도지코인": "dogecoin", "리플": "ripple",
    "솔라나": "solana", "비앤비": "binancecoin", "에이다": "cardano",
}


def normalize_crypto_symbol(text: str) -> str | None:
    """'비트코인'/'BTC' → CoinGecko id. 매칭 없으면 None."""
    t = str(text or "").strip().lower()
    if not t:
        return None
    if t in CRYPTO_NAMES:
        return CRYPTO_NAMES[t]
    for ko, cg_id in CRYPTO_NAMES.items():
        if ko in t:
            return cg_id
    if re.fullmatch(r"[a-z0-9\-]{2,20}", t):
        return t
    return None


def normalize_stock_symbol(text: str) -> str | None:
    """'삼성전자'/'AAPL' → Yahoo 심볼 (한국 주식은 .KS 접미)."""
    t = str(text or "").strip().upper()
    if not t:
        return None
    mapping = {"삼성전자": "005930.KS", "네이버": "035420.KS", "카카오": "035720.KS",
               "테슬라": "TSLA", "애플": "AAPL", "엔비디아": "NVDA"}
    if t in mapping:
        return mapping[t]
    for ko, sym in mapping.items():
        if ko in t:
            return sym
    if re.fullmatch(r"[A-Z]{1,5}(\.[A-Z]{1,2})?", t):
        return t
    return None


def parse_coingecko(data: dict[str, Any], cg_id: str, vs: str = "usd") -> float | None:
    """CoinGecko simple/price 응답 파싱."""
    try:
        return float(data[cg_id][vs])
    except (KeyError, TypeError, ValueError):
        return None


def parse_yahoo(data: dict[str, Any]) -> float | None:
    """Yahoo chart 응답 파싱 (meta.regularMarketPrice)."""
    try:
        result = (data.get("chart") or {}).get("result") or []
        if not result:
            return None
        price = result[0].get("meta", {}).get("regularMarketPrice")
        return float(price) if price is not None else None
    except (TypeError, ValueError, IndexError):
        return None


def format_briefing(quotes: list[dict[str, Any]]) -> str:
    """시세 목록 → 한 줄 브리핑 텍스트."""
    if not quotes:
        return "시세 정보가 없습니다."
    lines = ["📈 시세 브리핑:"]
    for q in quotes:
        name = q.get("name", "?")
        price = q.get("price")
        change = q.get("change")
        if price is None:
            lines.append(f"- {name}: 조회 실패")
            continue
        change_str = f" ({change:+.1f}%)" if change is not None else ""
        lines.append(f"- {name}: {price:,.2f}{change_str}")
    return "\n".join(lines)


def extract_market_command(text: str) -> dict[str, Any] | None:
    """'비트코인 시세' / '삼성전자 주가' / '시세 브리핑' 해석."""
    t = str(text or "")
    if not re.search(r"시세|주가|가격|브리핑", t):
        return None
    crypto = normalize_crypto_symbol(t)
    if crypto:
        return {"kind": "crypto", "symbols": [crypto]}
    stock = normalize_stock_symbol(t)
    if stock:
        return {"kind": "stock", "symbols": [stock]}
    return {"kind": "mixed", "symbols": []}
