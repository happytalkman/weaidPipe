"""Proactive Ambient Vision Observer for WEAID AGI."""
from __future__ import annotations

import hashlib
import io
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

BASE_DIR = Path(__file__).resolve().parent.parent


@dataclass
class ScreenFrame:
    timestamp: float
    window_title: str
    image_bytes: bytes
    frame_hash: str
    width: int = 0
    height: int = 0


class VisionObserver:
    """Captures and filters screen frames with smart delta hashing to avoid redundant processing."""

    def __init__(self, capture_fn: Callable[[], tuple[bytes, str, int, int]] | None = None):
        self.capture_fn = capture_fn
        self._last_frame_hash = ""
        self._last_capture_time = 0.0

    def capture_frame(self) -> ScreenFrame | None:
        """Capture current screen frame and active window title."""
        if self.capture_fn:
            try:
                img_bytes, title, w, h = self.capture_fn()
                f_hash = hashlib.sha256(img_bytes[:4096]).hexdigest()
                return ScreenFrame(
                    timestamp=time.time(),
                    window_title=title,
                    image_bytes=img_bytes,
                    frame_hash=f_hash,
                    width=w,
                    height=h,
                )
            except Exception:
                return None

        # Default real screen capture via mss or PIL
        try:
            import mss
            with mss.mss() as sct:
                monitor = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
                sct_img = sct.grab(monitor)
                import PIL.Image as Image
                img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=70)
                img_bytes = buf.getvalue()
                f_hash = hashlib.sha256(img_bytes[:4096]).hexdigest()

                title = ""
                try:
                    import pygetwindow as gw
                    active_win = gw.getActiveWindow()
                    title = active_win.title if active_win else ""
                except Exception:
                    title = "Desktop"

                return ScreenFrame(
                    timestamp=time.time(),
                    window_title=title,
                    image_bytes=img_bytes,
                    frame_hash=f_hash,
                    width=img.width,
                    height=img.height,
                )
        except Exception:
            return None

    def has_significant_change(self, frame: ScreenFrame) -> bool:
        """Check if frame content has changed compared to last captured state."""
        if frame.frame_hash != self._last_frame_hash:
            self._last_frame_hash = frame.frame_hash
            return True
        return False

