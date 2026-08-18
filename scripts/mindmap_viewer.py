"""Standalone mindmap / ontology viewer (separate process).

QtWebEngine is crash-prone when created inside the main JARVIS application
process, so it runs here in its own process. If Chromium dies on this machine,
only this popup window is lost — the main app keeps running.

Usage:
    python scripts/mindmap_viewer.py <data.json> [mindmap|graph]
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Force software rendering BEFORE QApplication exists.
os.environ["QT_OPENGL"] = "software"
os.environ.setdefault(
    "QTWEBENGINE_CHROMIUM_FLAGS",
    "--disable-gpu --disable-gpu-compositing --use-angle=swiftshader --no-sandbox",
)

BASE_DIR = Path(__file__).resolve().parent.parent
HTML_PATH = BASE_DIR / "web" / "mindmap.html"


def main() -> int:
    data_path = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    mode = sys.argv[2] if len(sys.argv) > 2 else "mindmap"

    from PyQt6.QtCore import QUrl
    from PyQt6.QtWidgets import (
        QApplication, QHBoxLayout, QLabel, QMainWindow, QPushButton, QVBoxLayout, QWidget,
    )
    from PyQt6.QtWebEngineWidgets import QWebEngineView

    app = QApplication(sys.argv)
    win = QMainWindow()
    win.setWindowTitle("WEAID · Mindmap / Ontology")
    win.resize(1280, 860)

    root = QWidget()
    lay = QVBoxLayout(root)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(0)

    # ── Toolbar: switch between the two views ─────────────────────────────
    bar = QWidget()
    bar.setStyleSheet("background:#0a1224; border-bottom:1px solid #1e2a44;")
    bar_lay = QHBoxLayout(bar)
    bar_lay.setContentsMargins(12, 8, 12, 8)
    lbl = QLabel("WEAID · 마인드맵 / 온톨로지")
    lbl.setStyleSheet("color:#7ee0ff; font-weight:bold; border:none;")
    bar_lay.addWidget(lbl)
    bar_lay.addStretch(1)

    def _make_btn(text: str, m: str) -> QPushButton:
        b = QPushButton(text)
        b.setFixedSize(150, 36)
        b.setStyleSheet(
            "QPushButton {"
            " background:#0d2b1c; color:#51cf66;"
            " border:1px solid #1e5e33; border-radius:5px; padding:0 12px;"
            "}"
            "QPushButton:hover { background:#0a1a12; border:1px solid #51cf66; }"
        )
        b.clicked.connect(lambda: _render(m))
        return b

    mm_btn = _make_btn("MINDMAP", "mindmap")
    og_btn = _make_btn("ONTOLOGY", "graph")
    bar_lay.addWidget(mm_btn)
    bar_lay.addSpacing(8)
    bar_lay.addWidget(og_btn)
    bar_lay.addSpacing(14)

    rdf_btn = QPushButton("RDF/OWL2")
    rdf_btn.setFixedSize(110, 36)
    rdf_btn.setStyleSheet(
        "QPushButton { background:#1a1430; color:#b49dff;"
        " border:1px solid #4a3a80; border-radius:5px; padding:0 10px; }"
        "QPushButton:hover { background:#120d24; border:1px solid #b49dff; }"
    )

    def _export_rdf():
        try:
            from core.rdf_export import export_ontology
            path = export_ontology()
            rdf_btn.setText("RDF ✅")
            rdf_btn.setToolTip(f"내보내기 완료: {path}")
        except Exception as e:
            rdf_btn.setToolTip(f"내보내기 실패: {str(e)[:120]}")

    # 포커스 상태 (그래프 탐색용) — 버튼/폴링이 공유하는 홀더
    import tempfile as _tf
    focus_path = (Path(_tf.gettempdir()) / "weaid_mindmap_focus.json") if data_path is not None else None
    focus_state = {"data": {}}

    def _clear_focus():
        focus_state["data"] = {}
        try:
            if focus_path is not None and focus_path.exists():
                focus_path.unlink()
        except Exception:
            pass
        _render(state["mode"])

    rdf_btn.setToolTip("누적 대화 그래프를 RDF/OWL2(Turtle)로 내보내기")
    rdf_btn.clicked.connect(_export_rdf)
    bar_lay.addWidget(rdf_btn)
    bar_lay.addSpacing(8)

    clear_btn = QPushButton("전체 보기")
    clear_btn.setFixedSize(96, 36)
    clear_btn.setStyleSheet(
        "QPushButton { background:#101826; color:#8b9bc0;"
        " border:1px solid #22304f; border-radius:5px; padding:0 10px; }"
        "QPushButton:hover { background:#0a1220; border:1px solid #8b9bc0; }"
    )
    clear_btn.setToolTip("탐색 포커스를 해제하고 전체 그래프 보기")
    clear_btn.clicked.connect(_clear_focus)
    bar_lay.addWidget(clear_btn)
    lay.addWidget(bar)

    view = QWebEngineView(root)
    lay.addWidget(view, 1)
    win.setCentralWidget(root)

    payload: dict = {}
    if data_path is not None and data_path.exists():
        try:
            payload = json.loads(data_path.read_text(encoding="utf-8"))
        except Exception:
            payload = {}

    html = (
        HTML_PATH.read_text(encoding="utf-8")
        if HTML_PATH.exists()
        else "<body style='background:#05070d;color:#dbe4ff'><h2>mindmap.html not found</h2></body>"
    )

    state = {"mtime": -1.0, "focus_mtime": -1.0, "mode": mode}

    def _render(m: str) -> None:
        state["mode"] = m
        data_js = json.dumps(payload, ensure_ascii=False)
        focus_js = json.dumps(focus_state["data"], ensure_ascii=False)
        if m == "graph":
            view.page().runJavaScript(f"renderGraph({data_js}, {focus_js})")
        else:
            view.page().runJavaScript(f"renderMindmap({data_js})")

    def _on_load(ok: bool) -> None:
        if ok:
            _render(mode)

    view.loadFinished.connect(_on_load)
    view.setHtml(html, QUrl.fromLocalFile(str(HTML_PATH)))
    win.show()

    # ── 실시간 동기화: 2초마다 공유 데이터 파일을 감시해서 자동 재렌더링 ──
    from PyQt6.QtCore import QTimer

    def _poll():
        try:
            nonlocal payload
            changed = False
            if data_path is not None and data_path.exists():
                mtime = data_path.stat().st_mtime
                if mtime != state["mtime"]:
                    state["mtime"] = mtime
                    try:
                        payload = json.loads(data_path.read_text(encoding="utf-8"))
                    except Exception:
                        return
                    changed = True
            if focus_path is not None and focus_path.exists():
                fmtime = focus_path.stat().st_mtime
                if fmtime != state["focus_mtime"]:
                    state["focus_mtime"] = fmtime
                    try:
                        focus_state["data"] = json.loads(focus_path.read_text(encoding="utf-8"))
                    except Exception:
                        focus_state["data"] = {}
                    changed = True
            if changed:
                _render(state["mode"])
        except Exception:
            pass

    poll_timer = QTimer()
    poll_timer.timeout.connect(_poll)
    poll_timer.start(2000)

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
