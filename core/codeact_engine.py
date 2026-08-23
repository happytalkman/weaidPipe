"""Autonomous CodeAct engine for dynamic code synthesis, sandboxed execution, and self-debugging."""
from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

BASE_DIR = Path(__file__).resolve().parent.parent


@dataclass
class CodeActResult:
    success: bool
    output: str
    error: str = ""
    code: str = ""
    execution_time: float = 0.0
    attempts: int = 1
    artifacts: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "output": self.output,
            "error": self.error,
            "code": self.code,
            "execution_time": self.execution_time,
            "attempts": self.attempts,
            "artifacts": self.artifacts,
        }


class CodeActEngine:
    """Executes Python code dynamically in a sandboxed subprocess with automated self-repair."""

    def __init__(self, python_path: str | Path | None = None, work_dir: str | Path | None = None):
        if python_path:
            self.python_path = Path(python_path)
        else:
            venv_py = BASE_DIR / ".venv" / "Scripts" / "python.exe"
            self.python_path = venv_py if venv_py.exists() else Path(sys.executable)

        self.work_dir = Path(work_dir) if work_dir else BASE_DIR

    def execute_code(
        self,
        code: str,
        timeout_s: float = 30.0,
        env_vars: dict[str, str] | None = None,
    ) -> CodeActResult:
        """Execute raw Python code in an isolated subprocess, capturing stdout/stderr."""
        code = self._clean_code(code)

        try:
            ast.parse(code)
        except SyntaxError as e:
            return CodeActResult(
                success=False,
                output="",
                error=f"SyntaxError: {e}",
                code=code,
                attempts=1,
            )

        start_time = time.monotonic()
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.py', delete=False, encoding='utf-8'
        ) as tmp:
            tmp.write(code)
            tmp_path = Path(tmp.name)

        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        if env_vars:
            env.update(env_vars)

        try:
            proc = subprocess.run(
                [str(self.python_path), str(tmp_path)],
                cwd=str(self.work_dir),
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=timeout_s,
                env=env,
            )
            elapsed = time.monotonic() - start_time
            out = proc.stdout.strip()
            err = proc.stderr.strip()

            if proc.returncode == 0:
                return CodeActResult(
                    success=True,
                    output=out or "(Code executed successfully with no stdout output)",
                    error="",
                    code=code,
                    execution_time=round(elapsed, 3),
                    attempts=1,
                )
            else:
                return CodeActResult(
                    success=False,
                    output=out,
                    error=err or f"Process exited with returncode {proc.returncode}",
                    code=code,
                    execution_time=round(elapsed, 3),
                    attempts=1,
                )
        except subprocess.TimeoutExpired:
            elapsed = time.monotonic() - start_time
            return CodeActResult(
                success=False,
                output="",
                error=f"Execution timed out after {timeout_s}s",
                code=code,
                execution_time=round(elapsed, 3),
                attempts=1,
            )
        except Exception as e:
            elapsed = time.monotonic() - start_time
            return CodeActResult(
                success=False,
                output="",
                error=f"Execution failed: {str(e)}",
                code=code,
                execution_time=round(elapsed, 3),
                attempts=1,
            )
        finally:
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except Exception:
                pass

    def synthesize_code(self, goal: str, context: dict[str, Any] | None = None) -> str:
        """Synthesize Python code to accomplish a user goal using unified LLM."""
        from core.llm import generate

        ctx_str = json.dumps(context or {}, ensure_ascii=False, indent=2) if context else "{}"

        system_prompt = (
            "You are an expert autonomous Python coding agent for WEAID AGI.\n"
            "Generate ONLY valid, self-contained, executable Python 3 code that accomplishes the user goal.\n"
            "Do NOT wrap with markdown commentary. Output purely the code or inside a single ```python code block.\n"
            "Make sure to print final results or outputs clearly to stdout."
        )

        user_prompt = f"Goal:\n{goal}\n\nContext:\n{ctx_str}\n\nWrite Python code:"
        resp = generate(system_prompt, user_prompt, timeout_s=45)
        return self._clean_code(resp)

    def repair_code(self, code: str, error_msg: str, goal: str) -> str:
        """Self-debugging: analyze the error traceback and synthesize a corrected version."""
        from core.llm import generate

        system_prompt = (
            "You are an expert autonomous Python debugger.\n"
            "Analyze the given failed code and the error/traceback, and produce the FIXED, working Python code.\n"
            "Output ONLY the corrected Python code without surrounding explanations."
        )

        user_prompt = (
            f"Goal:\n{goal}\n\n"
            f"Failed Code:\n```python\n{code}\n```\n\n"
            f"Error / Traceback:\n{error_msg}\n\n"
            "Corrected Code:"
        )

        resp = generate(system_prompt, user_prompt, timeout_s=45)
        return self._clean_code(resp)

    def run_codeact(
        self,
        goal: str,
        context: dict[str, Any] | None = None,
        max_retries: int = 3,
        timeout_s: float = 30.0,
        progress_cb: Callable[[str], None] | None = None,
    ) -> CodeActResult:
        """End-to-end CodeAct: Synthesize, execute, and self-debug in a feedback loop."""
        if progress_cb:
            progress_cb("Synthesizing code...")

        code = self.synthesize_code(goal, context)
        attempts = 0

        while attempts < max_retries:
            attempts += 1
            if progress_cb:
                progress_cb(f"Executing code (Attempt {attempts}/{max_retries})...")

            result = self.execute_code(code, timeout_s=timeout_s)
            result.attempts = attempts

            if result.success:
                if progress_cb:
                    progress_cb("Execution succeeded!")
                return result

            if attempts < max_retries:
                if progress_cb:
                    progress_cb(f"Execution error encountered: {result.error[:80]}... Repairing...")
                try:
                    code = self.repair_code(code, result.error, goal)
                except Exception as e:
                    result.error += f" | (Repair failed: {str(e)})"
                    break


        return result

    @staticmethod
    def _clean_code(raw: str) -> str:
        text = raw.strip()
        pattern = r"```(?:python)?\s*([\s\S]*?)\s*```"
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            text = match.group(1).strip()
        return text
