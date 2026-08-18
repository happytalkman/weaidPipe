"""WEAID self-evolution CLI (자가개선 실행기).

Usage:
    python scripts/self_improve.py              # report-only cycle (no changes)
    python scripts/self_improve.py --apply      # apply safe fixes + restart
    python scripts/self_improve.py --no-ai      # skip the Gemini proposal step
    python scripts/self_improve.py --watch      # continuous cycles every 10min
    python scripts/self_improve.py --apply --watch=5   # auto-fix every 5min
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from core.self_improve import run_cli  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(run_cli(sys.argv[1:]))
