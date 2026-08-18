"""Unified LLM client with automatic provider fallback (Gemini → Grok).

Every text-generation path in WEAID goes through here:

    1) Gemini (google.genai) — primary
    2) Grok    (xAI, OpenAI-compatible) — automatic fallback

If the Gemini key fails for any reason (quota, network, 1008, invalid key...),
the same request is retried on Grok so the system keeps working.

JSON mode is supported on both providers.
"""
from __future__ import annotations

import json
import os
from typing import Any

_GROK_MODELS = ("grok-4", "grok-3")
_GROK_BASE_URL = "https://api.x.ai/v1"


def gemini_key() -> str:
    return os.environ.get("GEMINI_API_KEY", "").strip()


def grok_key() -> str:
    return os.environ.get("XAI_API_KEY", "").strip()


def _call_gemini(system: str, prompt: str, json_mode: bool, timeout_ms: int) -> str:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=gemini_key(), http_options=types.HttpOptions(timeout=timeout_ms))
    config = types.GenerateContentConfig(
        system_instruction=system or None,
        temperature=0.2,
    )
    if json_mode:
        config.response_mime_type = "application/json"
    resp = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config=config,
    )
    return resp.text or ""


def _call_grok(system: str, prompt: str, json_mode: bool, timeout_s: int) -> str:
    from openai import OpenAI

    client = OpenAI(api_key=grok_key(), base_url=_GROK_BASE_URL, timeout=timeout_s)
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    kwargs: dict[str, Any] = {
        "messages": messages,
        "temperature": 0.2,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    last_err: Exception | None = None
    for model in _GROK_MODELS:
        try:
            resp = client.chat.completions.create(model=model, **kwargs)
            return (resp.choices[0].message.content or "") if resp.choices else ""
        except Exception as e:  # noqa: BLE001
            last_err = e
    raise last_err or RuntimeError("grok call failed")


def generate(system: str, prompt: str, json_mode: bool = False, timeout_s: int = 60) -> str:
    """Generate text with Gemini, falling back to Grok on any failure."""
    timeout_ms = max(1000, int(timeout_s * 1000))
    errors: list[str] = []

    if gemini_key():
        try:
            return _call_gemini(system, prompt, json_mode, timeout_ms)
        except Exception as e:  # noqa: BLE001
            errors.append(f"gemini: {str(e)[:120]}")

    if grok_key():
        try:
            return _call_grok(system, prompt, json_mode, timeout_s)
        except Exception as e:  # noqa: BLE001
            errors.append(f"grok: {str(e)[:120]}")

    raise RuntimeError("LLM providers unavailable: " + " | ".join(errors))


def generate_json(system: str, prompt: str, timeout_s: int = 60) -> dict[str, Any]:
    """Generate JSON with Gemini → Grok fallback and parse it."""
    raw = generate(system, prompt, json_mode=True, timeout_s=timeout_s)
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    raise ValueError("provider returned non-JSON payload")
