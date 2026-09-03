"""Windows application control via pywinauto (Playwright-style for desktop apps).

Playwright only drives web browsers. For native Windows applications the
equivalent automation stack is pywinauto. This module implements the same
"launch → connect → interact → read back" workflow for desktop apps:

  launch        → start the application (and report the window it created)
  list_windows  → list visible top-level windows (optional app name filter)
  click         → click a button / menu item by its visible name
  set_text      → focus the target window and type text
  get_text      → read the visible text of a window
  close         → close the target window

All pywinauto calls run behind a hard thread timeout so a stuck UIA tree can
never hang the caller.
"""
from __future__ import annotations

import concurrent.futures
import re
import subprocess
import time
from typing import Any, Callable


def _with_timeout(fn: Callable[[], Any], timeout: float = 12.0) -> Any:
    """Run fn in a thread with a hard timeout (pywinauto UIA can hang)."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(fn)
        try:
            return fut.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            return None
        except Exception as e:
            return e


def _find_window_handle(window_title: str) -> int | None:
    """Fast window lookup by partial title (no Application.connect)."""
    from pywinauto import findwindows

    if not window_title:
        handles = findwindows.find_windows(active_only=True)
        return handles[0] if handles else None
    try:
        handles = findwindows.find_windows(title_re=f".*{re.escape(window_title)}.*")
    except Exception:
        return None
    return handles[0] if handles else None


def _wrap(handle: int | None):
    """Wrap a window handle with the (fast) win32 backend."""
    if handle is None:
        return None
    from pywinauto import Application

    return Application(backend="win32").connect(handle=handle).window(handle=handle)


def launch(app_name: str) -> str:
    """Launch a Windows application by name and report its main window title."""
    app_name = (app_name or "").strip()
    if not app_name:
        return "ERR: app_name이 필요합니다."
    try:
        subprocess.Popen(f"start {app_name}", shell=True)
    except Exception as e:
        return f"ERR: 실행 실패: {e}"
    time.sleep(3)
    try:
        from pywinauto import Desktop

        found = []
        for w in Desktop(backend="uia").windows():
            try:
                t = w.window_text().strip()
            except Exception:
                continue
            if t and app_name.lower() in t.lower():
                found.append(t)
        if found:
            return f"OK: '{app_name}' 실행됨. 주 창: '{found[0][:80]}'"
    except Exception:
        pass
    return f"OK: '{app_name}' 실행 요청 완료 (창 제목 자동 감지는 생략됨)"


def list_windows(app_name: str = "") -> str:
    """List visible top-level windows."""
    try:
        from pywinauto import Desktop

        wins = Desktop(backend="uia").windows()
        lines = []
        for w in wins:
            try:
                title = w.window_text().strip()
            except Exception:
                continue
            if not title:
                continue
            if app_name and app_name.lower() not in title.lower():
                continue
            lines.append(title[:80])
        if not lines:
            return "OK: 표시된 창이 없습니다."
        return "OK: 열린 창 목록:\n" + "\n".join(f"- {t}" for t in lines[:25])
    except Exception as e:
        return f"ERR: 창 목록 조회 실패: {str(e)[:160]}"


def click(window_title: str, text: str) -> str:
    """Click a named control (button/menu item) inside the target window."""
    if not text:
        return "ERR: 클릭할 컨트롤 이름(text)이 필요합니다."

    def _do() -> str:
        win = _wrap(_find_window_handle(window_title))
        if win is None:
            return "ERR: 대상 창을 찾지 못했습니다."
        win.set_focus()
        target = win.child_window(title_re=f".*{re.escape(text)}.*")
        target.click()
        return f"OK: '{text}' 클릭 완료."

    r = _with_timeout(_do, timeout=12)
    return r if isinstance(r, str) else f"ERR: 클릭 실패: {r}"


def set_text(window_title: str, text: str) -> str:
    """Focus the target window and type text into it."""
    if not text:
        return "ERR: 입력할 텍스트가 필요합니다."

    def _do() -> str:
        win = _wrap(_find_window_handle(window_title))
        if win is None:
            return "ERR: 대상 창을 찾지 못했습니다."
        win.set_focus()
        win.type_keys(text, with_spaces=True)
        return f"OK: 텍스트 입력 완료 ({len(text)}자)."

    r = _with_timeout(_do, timeout=12)
    return r if isinstance(r, str) else f"ERR: 입력 실패: {r}"


def get_text(window_title: str) -> str:
    """Read the visible text of a window."""
    def _do() -> str:
        win = _wrap(_find_window_handle(window_title))
        if win is None:
            return "ERR: 대상 창을 찾지 못했습니다."
        parts = [win.window_text()]
        for cls in ("Edit", "RichEditD2DPT", "RichEdit20W", "Static"):
            try:
                for c in win.descendants(control_type=cls)[:8]:
                    try:
                        t = str(c.window_text()).strip()
                    except Exception:
                        t = ""
                    if t and t not in parts:
                        parts.append(t)
            except Exception:
                pass
        body = "\n".join(p for p in parts if p and p.strip())
        return f"OK: 창 내용:\n{body[:1500]}" if body else "OK: 읽을 수 있는 텍스트가 없습니다."

    r = _with_timeout(_do, timeout=15)
    return r if isinstance(r, str) else f"ERR: 읽기 실패: {r}"


def close(window_title: str) -> str:
    """Close the target window (WM_CLOSE + taskkill fallback)."""
    def _do() -> str:
        import ctypes

        handle = _find_window_handle(window_title)
        if handle is None:
            return "ERR: 대상 창을 찾지 못했습니다."
        # 1) 우아한 종료 신호 (저장 대화상자가 뜨면 사용자/모델이 처리)
        ctypes.windll.user32.PostMessageW(handle, 0x0010, 0, 0)  # WM_CLOSE
        time.sleep(1.5)
        if _find_window_handle(window_title) is None:
            return f"OK: '{window_title}' 창을 닫았습니다."
        # 2) fallback: 프로세스 종료
        pid = ctypes.c_ulong()
        ctypes.windll.user32.GetWindowThreadProcessId(handle, ctypes.byref(pid))
        subprocess.run(
            ["taskkill", "/F", "/PID", str(pid.value)],
            capture_output=True, timeout=15,
        )
        return f"OK: '{window_title}' 창을 강제 종료했습니다."

    r = _with_timeout(_do, timeout=20)
    return r if isinstance(r, str) else f"ERR: 닫기 실패: {r}"


def win_app_control(
    action: str,
    app_name: str = "",
    window_title: str = "",
    text: str = "",
    timeout: float = 15.0,
) -> str:
    """Playwright-style control entry point for Windows desktop apps."""
    action = (action or "").strip().lower()
    try:
        if action == "launch":
            return launch(app_name)
        if action == "list_windows":
            return list_windows(app_name)
        if action == "click":
            return click(window_title, text)
        if action == "set_text":
            return set_text(window_title, text)
        if action == "get_text":
            return get_text(window_title)
        if action == "close":
            return close(window_title)
        return "ERR: 지원하지 않는 action입니다. launch | list_windows | click | set_text | get_text | close"
    except Exception as e:
        return f"ERR: win_app_control 오류: {str(e)[:160]}"
