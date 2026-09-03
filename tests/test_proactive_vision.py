"""Unit tests for Phase 3: Proactive Ambient Vision & Context Awareness."""
import time
import pytest
from unittest.mock import patch, MagicMock

from awareness.vision_observer import ScreenFrame, VisionObserver
from awareness.proactive_agent import ProactiveAdvisor, ProactiveSuggestion


def test_vision_observer_delta_hashing():
    # Frame 1: blue image
    f1_bytes = b"IMAGE_DATA_1_BLUE" * 100
    # Frame 2: same blue image
    f2_bytes = b"IMAGE_DATA_1_BLUE" * 100
    # Frame 3: red image
    f3_bytes = b"IMAGE_DATA_2_RED" * 100

    frames = [
        (f1_bytes, "Visual Studio Code", 1920, 1080),
        (f2_bytes, "Visual Studio Code", 1920, 1080),
        (f3_bytes, "Error Window - Terminal", 1920, 1080),
    ]

    idx = 0
    def mock_capture():
        nonlocal idx
        item = frames[idx]
        idx = min(idx + 1, len(frames) - 1)
        return item

    observer = VisionObserver(capture_fn=mock_capture)

    # First capture -> change detected
    frame1 = observer.capture_frame()
    assert frame1 is not None
    assert observer.has_significant_change(frame1) is True

    # Second capture (same content) -> no change detected
    frame2 = observer.capture_frame()
    assert frame2 is not None
    assert observer.has_significant_change(frame2) is False

    # Third capture (different content) -> change detected
    frame3 = observer.capture_frame()
    assert frame3 is not None
    assert observer.has_significant_change(frame3) is True


def test_proactive_advisor_error_trigger():
    advisor = ProactiveAdvisor(cooldown_seconds=30.0)
    frame = ScreenFrame(
        timestamp=time.time(),
        window_title="Python Traceback Error - Terminal",
        image_bytes=b"sample_bytes",
        frame_hash="hash123",
    )

    mock_proposal = {
        "title": "Fix Python ModuleNotFoundError",
        "description": "Terminal shows missing 'requests' module",
        "suggested_action": "pip install requests",
        "confidence": 0.95,
        "trigger_reason": "Terminal error detected in active window",
    }

    with patch("core.llm.generate_json", return_value=mock_proposal):
        suggestion = advisor.evaluate_context(frame, text_context="ModuleNotFoundError: No module named 'requests'")
        assert suggestion is not None
        assert suggestion.title == "Fix Python ModuleNotFoundError"
        assert suggestion.suggested_action == "pip install requests"
        assert suggestion.confidence == 0.95
        assert len(advisor.get_history()) == 1


def test_proactive_advisor_cooldown():
    advisor = ProactiveAdvisor(cooldown_seconds=60.0)
    frame = ScreenFrame(
        timestamp=time.time(),
        window_title="Bug Report",
        image_bytes=b"sample",
        frame_hash="hash456",
    )

    mock_proposal = {
        "title": "Proposal",
        "description": "Desc",
        "suggested_action": "Act",
        "confidence": 0.8,
        "trigger_reason": "Context",
    }

    with patch("core.llm.generate_json", return_value=mock_proposal):
        s1 = advisor.evaluate_context(frame, text_context="error 1", force=False)
        assert s1 is not None

        # Immediate next call should be suppressed by cooldown
        s2 = advisor.evaluate_context(frame, text_context="error 2", force=False)
        assert s2 is None

        # Forced call bypasses cooldown
        s3 = advisor.evaluate_context(frame, text_context="error 3", force=True)
        assert s3 is not None

