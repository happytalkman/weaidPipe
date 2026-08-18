"""Self-evolution engine for WEAID (자가개선 엔진).

Runs a closed improvement loop every cycle:

    PERCEIVE  ->  ANALYZE  ->  DECIDE  ->  ACT  ->  VERIFY  ->  RECORD

  1. PERCEIVE  deterministic health checks over the project
               (compile, process, logs, assets, config, branding).
  2. ANALYZE   each finding is scored (critical / high / low) and tagged.
  3. DECIDE    select fixes: deterministic templates first, then AI-proposed
               exact-match patches.
  4. ACT       apply each fix behind backup + compile-verify + rollback.
  5. VERIFY    re-run the affected checks and compare.
  6. RECORD    persist a JSON report + markdown summary.

Safety rails:
  * Report-only by default — nothing changes without apply=True.
  * Every patched file is backed up first.
  * AI patches must be exact-match replacements that occur exactly once in
    the target file, and the file must still compile after the patch,
    otherwise the change is rolled back automatically.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
ARTIFACT_DIR = BASE_DIR / ".qa-artifacts" / "self-improve"
BACKUP_DIR = ARTIFACT_DIR / "backups"
REPORT_DIR = ARTIFACT_DIR / "reports"

PYTHON = sys.executable or "python"

# Files the engine may patch. Everything else is read-only for the engine.
_PATCHABLE_SUFFIXES = (".py", ".html", ".txt", ".md", ".json")
_EXCLUDED_DIRS = {".git", ".venv", "venv", "__pycache__", ".qa-artifacts", "node_modules"}

# Branding words that must not appear in user-visible strings.
_BRANDING_LEFTOVERS = ("JARVIS", "Google", "Gemini", "DeepMind")
# Lines that are intentionally allowed to mention them (comments, runtime
# replacements, env var names, tool names, and the identity instruction itself).
_BRANDING_ALLOW_RE = re.compile(
    r"^\s*#|^\s*\"\"\"|prompt\.replace|never |Never |Always speak of the creator|"
    r"jarvis_ui_control|JARVIS_SKIP|JARVIS_CLI|"
    r"JARVIS_ENABLE|JARVIS_REQUIRE|JARVIS_LOCKED|JARVIS_TRIAL|JARVIS_RUNTIME|JARVIS_API_KEY|"
    r"from google|import google|GOOGLE_API_KEY|google_api_key",
    re.IGNORECASE,
)


@dataclass
class Issue:
    check: str
    severity: str          # critical | high | low
    title: str
    detail: str
    file: str = ""
    line: int = 0
    line_text: str = ""    # full original line (for exact-match fixes)
    fix: str = ""          # one of: restart_app | branding_replace | ai_patch | manual


@dataclass
class FixResult:
    issue_title: str
    action: str
    ok: bool
    note: str


def _project_python_files() -> list[Path]:
    files: list[Path] = []
    for root, dirs, names in os.walk(BASE_DIR):
        dirs[:] = [d for d in dirs if d not in _EXCLUDED_DIRS]
        for name in names:
            if name.endswith(".py"):
                files.append(Path(root) / name)
    return files


def _patchable(path: Path) -> bool:
    return path.is_file() and path.suffix in _PATCHABLE_SUFFIXES and path.exists()


# ── PERCEIVE: health checks ────────────────────────────────────────────────

def check_compile() -> list[Issue]:
    issues: list[Issue] = []
    for f in _project_python_files():
        rel = f.relative_to(BASE_DIR)
        try:
            subprocess.run(
                [PYTHON, "-m", "py_compile", str(f)],
                capture_output=True, timeout=60, cwd=str(BASE_DIR),
            )
        except Exception:
            pass
        err_path = f.with_suffix(".pyc")
        # py_compile writes .pyc silently on success; detect errors via a rerun
        r = subprocess.run(
            [PYTHON, "-m", "py_compile", str(f)],
            capture_output=True, text=True, timeout=60, cwd=str(BASE_DIR),
        )
        if r.returncode != 0:
            first_line = (r.stderr or "").strip().splitlines()
            msg = first_line[-1] if first_line else "compile error"
            issues.append(Issue(
                check="compile", severity="critical", title=f"컴파일 오류: {rel}",
                detail=msg, file=str(rel), fix="manual",
            ))
    return issues


def check_process() -> list[Issue]:
    issues: list[Issue] = []
    try:
        r = subprocess.run(
            [
                "powershell", "-NoProfile", "-Command",
                "(Get-Process jarvis -ErrorAction SilentlyContinue) | "
                "Measure-Object | Select-Object -ExpandProperty Count",
            ],
            capture_output=True, text=True, timeout=40,
        )
        count = int((r.stdout or "").strip() or "0")
    except Exception:
        count = 0
    if count == 0:
        issues.append(Issue(
            check="process", severity="critical", title="앱이 실행 중이 아닙니다",
            detail="jarvis.exe 프로세스가 없습니다. 자동 재시작합니다.",
            fix="restart_app",
        ))
    return issues


_BENIGN_LOG_RE = re.compile(
    r"GLES|gpu_channel|ContextResult|GPU stall|automatic function calling|Vulkan|ANGLE|swiftshader",
    re.IGNORECASE,
)


def check_logs() -> list[Issue]:
    issues: list[Issue] = []
    log_candidates = [
        Path(os.environ.get("TEMP", "/tmp")) / "jarvis-launch.log",
        Path(os.environ.get("TEMP", "/tmp")) / "weaid_mindmap_viewer.log",
        BASE_DIR / "jarvis-launch.log",
    ]
    for log in log_candidates:
        if not log.exists():
            continue
        try:
            text = log.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        meaningful = [
            l for l in text.splitlines()
            if re.search(r"(Traceback|Exception|Error|ERROR|segfault)", l)
            and not _BENIGN_LOG_RE.search(l)
        ]
        if meaningful:
            issues.append(Issue(
                check="logs", severity="high",
                title=f"로그에서 오류 {len(meaningful)}건 발견: {log.name}",
                detail=" | ".join(l.strip()[:160] for l in meaningful[-3:]),
                file=str(log), fix="manual",
            ))
    return issues


def check_assets() -> list[Issue]:
    required = [
        BASE_DIR / "web" / "mindmap.html",
        BASE_DIR / "web" / "d3.v7.min.js",
        BASE_DIR / "scripts" / "mindmap_viewer.py",
        BASE_DIR / "core" / "prompt.txt",
        BASE_DIR / ".env",
    ]
    issues = []
    for f in required:
        if not f.exists():
            issues.append(Issue(
                check="assets", severity="critical", title=f"필수 파일 없음: {f.name}",
                detail=str(f.relative_to(BASE_DIR)), file=str(f.relative_to(BASE_DIR)), fix="manual",
            ))
    return issues


def check_config() -> list[Issue]:
    issues: list[Issue] = []
    env = BASE_DIR / ".env"
    if env.exists():
        text = env.read_text(encoding="utf-8", errors="replace")
        if "YOUR_GEMINI_API_KEY" in text:
            issues.append(Issue(
                check="config", severity="critical",
                title=".env 에 API 키가 placeholder 상태입니다",
                detail="GEMINI_API_KEY=\"YOUR_GEMINI_API_KEY\" → 실제 키로 교체 필요.",
                file=".env", fix="manual",
            ))
    return issues


def check_branding() -> list[Issue]:
    issues: list[Issue] = []
    targets = [BASE_DIR / "ui.py", BASE_DIR / "main.py", BASE_DIR / "core" / "mindmap.py"]
    for f in targets:
        if not f.exists():
            continue
        for i, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if _BRANDING_ALLOW_RE.search(line):
                continue
            # user-visible string literals only (roughly)
            if not re.search(r"[\"'][^\"']*(JARVIS|Google|Gemini|DeepMind)", line):
                continue
            for word in _BRANDING_LEFTOVERS:
                if re.search(rf"[\"'][^\"']*\b{word}\b", line):
                    issues.append(Issue(
                        check="branding", severity="low",
                        title=f"노출 문자열에 '{word}' 잔존",
                        detail=line.strip()[:160],
                        file=str(f.relative_to(BASE_DIR)), line=i,
                        line_text=line,
                        fix="branding_replace",
                    ))
                    break
    return issues


# ── ANALYZE ────────────────────────────────────────────────────────────────

_SEVERITY_RANK = {"critical": 0, "high": 1, "low": 2}


def analyze(issues: list[Issue]) -> list[Issue]:
    return sorted(issues, key=lambda i: (_SEVERITY_RANK.get(i.severity, 3), i.check))


# ── DECIDE + ACT ───────────────────────────────────────────────────────────

def _backup(path: Path, stamp: str) -> Path:
    dst = BACKUP_DIR / stamp / path.name
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(path.read_bytes())
    return dst


def _restore(path: Path, backup: Path) -> None:
    path.write_bytes(backup.read_bytes())


def _compile_file(path: Path) -> bool:
    r = subprocess.run(
        [PYTHON, "-m", "py_compile", str(path)],
        capture_output=True, text=True, timeout=60, cwd=str(BASE_DIR),
    )
    return r.returncode == 0


def _apply_exact_patch(path: Path, old_text: str, new_text: str, stamp: str) -> FixResult:
    """Apply one exact-match replacement with backup/compile/rollback."""
    rel = str(path.relative_to(BASE_DIR))
    if not _patchable(path):
        return FixResult("", "patch", False, f"{rel}: 패치 불가 파일")
    try:
        content = path.read_text(encoding="utf-8")
    except Exception as e:
        return FixResult("", "patch", False, f"{rel}: 읽기 실패 {e}")
    count = content.count(old_text)
    if count != 1:
        return FixResult("", "patch", False, f"{rel}: old_text {count}회 일치 (정확히 1회여야 함)")
    backup = _backup(path, stamp)
    path.write_text(content.replace(old_text, new_text), encoding="utf-8")
    if path.suffix == ".py" and not _compile_file(path):
        _restore(path, backup)
        return FixResult("", "patch", False, f"{rel}: 컴파일 실패 → 롤백")
    return FixResult("", "patch", True, f"{rel}: 패치 적용 완료")


def _restart_app() -> FixResult:
    """Restart the desktop app in the background (Windows)."""
    try:
        launch = Path(os.environ.get("TEMP", "/tmp")) / "jarvis-relaunch.log"
        log_fh = open(launch, "a", encoding="utf-8")
        env = dict(os.environ)
        env["JARVIS_CLI"] = "1"
        env["JARVIS_SKIP_CLAP_GATE"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS if os.name == "nt" else 0
        # jarvis.exe 런처로 실행 (프로세스 검사와 동일한 이름 — 중복 방지)
        launcher = Path(PYTHON).parent / ("jarvis.exe" if os.name == "nt" else "jarvis")
        cmd = [str(launcher)] if launcher.exists() else [PYTHON, "-m", "main"]
        subprocess.Popen(
            cmd,
            cwd=str(BASE_DIR), env=env, stdout=log_fh, stderr=log_fh,
            creationflags=flags,
        )
        return FixResult("", "restart_app", True, "앱을 백그라운드로 재시작했습니다.")
    except Exception as e:
        return FixResult("", "restart_app", False, f"재시작 실패: {e}")


def _branding_fix(issue: Issue, stamp: str) -> FixResult | None:
    """Deterministic branding replacement for a detected leftover line."""
    path = BASE_DIR / issue.file
    line = issue.line_text
    if not path.exists() or not line:
        return None
    new_line = line
    new_line = new_line.replace("JARVIS", "AID")
    new_line = new_line.replace("Gemini", "WEAID")
    new_line = new_line.replace("Google", "WEAID")
    new_line = new_line.replace("DeepMind", "WEAID")
    if new_line == line:
        return None
    return _apply_exact_patch(path, line, new_line, stamp)


# ── AI proposals (Gemini) ──────────────────────────────────────────────────

_AI_PATCH_SYSTEM = (
    "You are the WEAID self-evolution engine. Given a list of detected issues "
    "and the relevant file contents, propose minimal fixes. Respond with JSON "
    "only, no markdown fences. Shape: "
    '{"patches":[{"file":"relative/path","old_text":"exact text from the file",'
    '"new_text":"replacement text","reason":"short reason"}]}. '
    "old_text must be copied verbatim from the provided file contents and must "
    "be unique in that file. Only patch files listed in the context. If no "
    "safe patch exists, return {\"patches\":[]}."
)


def ai_propose_patches(issues: list[Issue], limit: int = 5) -> list[dict[str, Any]]:
    """Ask Gemini for exact-match patches for the top issues (advisory)."""
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key or not issues:
        return []
    context_parts: list[str] = []
    shown: set[str] = set()
    for issue in issues[:limit]:
        if issue.file:
            path = BASE_DIR / issue.file
            if path.exists() and issue.file not in shown:
                shown.add(issue.file)
                try:
                    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
                    around = lines[max(0, issue.line - 15):issue.line + 15] if issue.line else lines[:60]
                    context_parts.append(f"FILE: {issue.file}\n" + "\n".join(
                        f"{i+1}: {l}" for i, l in enumerate(around)
                    ))
                except Exception:
                    pass
    if not context_parts:
        return []
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=60_000))
        resp = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=(
                "ISSUES:\n" + json.dumps([{k: getattr(i, k) for k in ("check", "severity", "title", "detail", "file", "line")} for i in issues[:limit]], ensure_ascii=False, indent=1)
                + "\n\nCONTEXT:\n" + "\n\n".join(context_parts)
            ),
            config=types.GenerateContentConfig(
                system_instruction=_AI_PATCH_SYSTEM,
                response_mime_type="application/json",
                temperature=0.1,
            ),
        )
        data = json.loads(resp.text or "{}")
        return data.get("patches", []) if isinstance(data, dict) else []
    except Exception:
        return []


# ── Engine ─────────────────────────────────────────────────────────────────

class SelfImprovementEngine:
    """One full self-evolution cycle."""

    def __init__(self, apply: bool = False, use_ai: bool = True):
        self.apply = apply
        self.use_ai = use_ai

    def run_cycle(self) -> dict[str, Any]:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        REPORT_DIR.mkdir(parents=True, exist_ok=True)

        # 1. PERCEIVE
        issues: list[Issue] = []
        for check in (check_compile, check_process, check_logs, check_assets, check_config, check_branding):
            try:
                issues.extend(check())
            except Exception as e:
                issues.append(Issue("engine", "low", "체크 실패", str(e), fix="manual"))

        # 2. ANALYZE
        issues = analyze(issues)

        # 3. DECIDE + 4. ACT
        results: list[FixResult] = []
        if self.apply:
            for issue in issues:
                if issue.fix == "restart_app":
                    results.append(_restart_app())
                elif issue.fix == "branding_replace":
                    r = _branding_fix(issue, stamp)
                    if r:
                        results.append(r)

        # AI-proposed patches (exact-match, applied only when apply=True).
        ai_patches: list[dict[str, Any]] = []
        if self.use_ai:
            ai_patches = ai_propose_patches([i for i in issues if i.severity in ("critical", "high")])
        ai_results: list[FixResult] = []
        if self.apply:
            for p in ai_patches:
                try:
                    path = BASE_DIR / str(p.get("file", ""))
                    old = str(p.get("old_text", ""))
                    new = str(p.get("new_text", ""))
                    r = _apply_exact_patch(path, old, new, stamp)
                    r.issue_title = str(p.get("reason", ""))[:100]
                    ai_results.append(r)
                except Exception as e:
                    ai_results.append(FixResult(str(p.get("reason", "")), "ai_patch", False, str(e)))

        # 5. VERIFY
        verify: dict[str, Any] = {}
        for check in (check_compile, check_process):
            try:
                remaining = check()
                verify[check.__name__] = {"ok": not remaining, "issues": [asdict(i) for i in remaining]}
            except Exception as e:
                verify[check.__name__] = {"ok": False, "issues": [str(e)]}

        # 5.5 EVOLVE — 헌법 진화 루프 (RLAIF 기반 보완 조항)
        evolution: dict[str, Any] | None = None
        try:
            from core.constitution_evolver import maybe_evolve
            evolution = maybe_evolve(force=False)
        except Exception:
            pass

        # 6. RECORD
        report: dict[str, Any] = {
            "stamp": stamp,
            "apply": self.apply,
            "issues": [asdict(i) for i in issues],
            "fixed": [asdict(r) for r in results],
            "ai_patches": ai_patches,
            "ai_results": [asdict(r) for r in ai_results],
            "verify": verify,
            "evolution": evolution,
        }
        report_path = REPORT_DIR / f"cycle-{stamp}.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

        md = _render_markdown(report)
        (REPORT_DIR / f"cycle-{stamp}.md").write_text(md, encoding="utf-8")

        report["report_path"] = str(report_path)
        return report


def _render_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# WEAID 자기개선 사이클 {report['stamp']}",
        "",
        f"- 적용 모드: {'실제 적용' if report['apply'] else '리포트 전용 (변경 없음)'}",
        f"- 발견된 문제: {len(report['issues'])}건",
        "",
        "## 발견된 문제",
    ]
    for i in report["issues"]:
        lines.append(f"- **[{i['severity']}] {i['title']}** — {i['detail'][:200]}")
    if report["fixed"]:
        lines.append("")
        lines.append("## 적용된 수정")
        for r in report["fixed"]:
            lines.append(f"- {'✅' if r['ok'] else '❌'} {r['note']}")
    if report["ai_patches"]:
        lines.append("")
        lines.append("## AI 제안 패치")
        for p in report["ai_patches"]:
            lines.append(f"- `{p.get('file')}`: {p.get('reason', '')}")
    lines.append("")
    lines.append("## 검증")
    for name, v in report.get("verify", {}).items():
        lines.append(f"- {name}: {'✅ 정상' if v['ok'] else '⚠️ 문제 잔존'}")
    return "\n".join(lines)


def run_watch(interval_minutes: int = 10, apply: bool = False, use_ai: bool = True) -> None:
    """Run self-improvement cycles continuously (scheduled self-evolution)."""
    print(f"[SELF-EVOLVE] 감시 모드 시작 — {interval_minutes}분 간격 (apply={apply})")
    engine = SelfImprovementEngine(apply=apply, use_ai=use_ai)
    while True:
        try:
            report = engine.run_cycle()
            n = len(report["issues"])
            n_fixed = len(report["fixed"]) + len(report["ai_results"])
            print(f"[SELF-EVOLVE] 사이클 완료 — 문제 {n}건, 수정 {n_fixed}건")
        except Exception as e:
            print(f"[SELF-EVOLVE] 사이클 오류: {e}")
        time.sleep(max(1, interval_minutes * 60))


def run_cli(argv: list[str]) -> int:
    if "--evolve" in argv:
        from core.constitution_evolver import run_evolve_cli
        return run_evolve_cli()
    apply = "--apply" in argv
    use_ai = "--no-ai" not in argv
    watch_arg = next((a for a in argv if a == "--watch" or a.startswith("--watch=")), None)
    if watch_arg is not None:
        m = 10
        if watch_arg.startswith("--watch="):
            try:
                m = max(1, int(watch_arg.split("=", 1)[1]))
            except ValueError:
                pass
        run_watch(m, apply=apply, use_ai=use_ai)
        return 0
    engine = SelfImprovementEngine(apply=apply, use_ai=use_ai)
    print(f"[SELF-EVOLVE] 사이클 시작 (apply={apply}, ai={use_ai})")
    report = engine.run_cycle()
    print(f"[SELF-EVOLVE] 발견된 문제 {len(report['issues'])}건")
    for i in report["issues"]:
        print(f"  [{i['severity']}] {i['title']}")
    for r in report["fixed"] + report["ai_results"]:
        print(f"  {'✅' if r['ok'] else '❌'} {r['note']}")
    evo = report.get("evolution")
    if evo and evo.get("applied"):
        print(f"[SELF-EVOLVE] 🧬 헌법 진화: {len(evo['applied'])}개 보완 조항 적용")
    print(f"[SELF-EVOLVE] 리포트: {report['report_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run_cli(sys.argv[1:]))
