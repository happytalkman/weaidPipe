"""Proactive Ambient Advisor for WEAID AGI."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from awareness.vision_observer import ScreenFrame, VisionObserver


@dataclass
class ProactiveSuggestion:
    title: str
    description: str
    suggested_action: str
    confidence: float
    trigger_reason: str
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "description": self.description,
            "suggested_action": self.suggested_action,
            "confidence": self.confidence,
            "trigger_reason": self.trigger_reason,
            "created_at": self.created_at,
        }


class ProactiveAdvisor:
    """Monitors ambient screen context and generates helpful, non-intrusive action proposals."""

    def __init__(self, cooldown_seconds: float = 60.0):
        self.cooldown_seconds = cooldown_seconds
        self._last_suggestion_time = 0.0
        self._history: list[ProactiveSuggestion] = []

    def evaluate_context(
        self,
        frame: ScreenFrame,
        text_context: str = "",
        force: bool = False,
    ) -> ProactiveSuggestion | None:
        """Analyze screen context and determine if proactive assistance is warranted."""
        now = time.time()
        if not force and (now - self._last_suggestion_time < self.cooldown_seconds):
            return None

        # Check for error patterns or critical conditions
        title = frame.window_title.lower() if frame.window_title else ""
        has_error_keyword = any(k in title for k in ("error", "exception", "failed", "crash", "bug", "traceback"))
        has_doc_keyword = any(k in title for k in ("report", "draft", "readme", "document", "proposal", "code"))

        if not (has_error_keyword or has_doc_keyword or text_context):
            return None

        from core.llm import generate_json

        sys_prompt = (
            "You are the Proactive Ambient Advisor for WEAID AGI.\n"
            "Evaluate the user's active window and context to propose an actionable assistance step.\n"
            "Return JSON with format:\n"
            "{\"title\": \"...\", \"description\": \"...\", \"suggested_action\": \"...\", \"confidence\": 0.9, \"trigger_reason\": \"...\"}\n"
            "If no meaningful suggestion is needed, return {\"title\": \"\"}."
        )

        usr_prompt = (
            f"Active Window: {frame.window_title}\n"
            f"Context: {text_context}\n"
            "Generate proactive proposal JSON:"
        )

        try:
            data = generate_json(sys_prompt, usr_prompt, timeout_s=25)
            if not data.get("title"):
                return None

            suggestion = ProactiveSuggestion(
                title=data.get("title", "Proactive Insight"),
                description=data.get("description", ""),
                suggested_action=data.get("suggested_action", ""),
                confidence=float(data.get("confidence", 0.8)),
                trigger_reason=data.get("trigger_reason", "Context observation"),
                created_at=now,
            )

            self._last_suggestion_time = now
            self._history.append(suggestion)
            return suggestion
        except Exception:
            return None

    def get_history(self) -> list[ProactiveSuggestion]:
        return list(self._history)

