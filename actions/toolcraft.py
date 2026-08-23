"""Dynamic Toolcrafting and Custom Tool Registry for WEAID AGI."""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

BASE_DIR = Path(__file__).resolve().parent.parent
CUSTOM_TOOLS_DIR = BASE_DIR / "custom_tools"

@dataclass
class CustomTool:
    name: str
    description: str
    parameters: dict[str, Any]
    code: str
    created_at: float = field(default_factory=time.time)
    usage_count: int = 0
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ToolCrafter:
    """Synthesizes, registers, persists, and executes custom dynamic tools."""

    def __init__(self, tools_dir: Path | None = None):
        self.tools_dir = tools_dir or CUSTOM_TOOLS_DIR
        self.tools_dir.mkdir(parents=True, exist_ok=True)
        self.registry_path = self.tools_dir / "registry.json"
        self._tools: dict[str, CustomTool] = {}
        self._load_registry()

    def _load_registry(self) -> None:
        if self.registry_path.exists():
            try:
                data = json.loads(self.registry_path.read_text(encoding="utf-8"))
                for k, v in data.items():
                    self._tools[k] = CustomTool(**v)
            except Exception:
                self._tools = {}

    def _save_registry(self) -> None:
        data = {k: v.to_dict() for k, v in self._tools.items()}
        self.registry_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def register_tool(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        code: str,
        tags: list[str] | None = None,
    ) -> CustomTool:
        """Validate and register a new tool, saving its code to disk."""
        clean_name = "".join(c for c in name.lower().strip().replace(" ", "_") if c.isalnum() or c == "_")
        if not clean_name or not any(c.isalnum() for c in clean_name):
            raise ValueError("Tool name must contain alphanumeric characters.")

        ast.parse(code)

        tool = CustomTool(
            name=clean_name,
            description=description,
            parameters=parameters or {},
            code=code,
            tags=tags or [],
        )

        tool_file = self.tools_dir / f"{clean_name}.py"
        tool_file.write_text(code, encoding="utf-8")

        self._tools[clean_name] = tool
        self._save_registry()
        return tool

    def get_tool(self, name: str) -> CustomTool | None:
        return self._tools.get(name)

    def list_tools(self) -> list[CustomTool]:
        return list(self._tools.values())

    def execute_tool(self, name: str, args: dict[str, Any] | None = None) -> Any:
        # Dynamically load and execute the registered tool main function
        tool = self.get_tool(name)
        if not tool:
            raise ValueError(f"Custom tool '{name}' is not registered.")

        tool_file = self.tools_dir / f"{name}.py"
        if not tool_file.exists():
            tool_file.write_text(tool.code, encoding="utf-8")

        spec = importlib.util.spec_from_file_location(f"custom_tool_{name}", str(tool_file))
        if spec is None or spec.loader is None:
            raise ImportError(f"Could not load spec for tool {name}")

        module = importlib.util.module_from_spec(spec)
        sys.modules[f"custom_tool_{name}"] = module
        spec.loader.exec_module(module)

        entry_func = getattr(module, "run", None) or getattr(module, "execute", None) or getattr(module, "main", None)
        if entry_func is None:
            raise AttributeError(f"Tool module '{name}' has no entrypoint (run, execute, or main).")

        tool.usage_count += 1
        self._save_registry()

        args = args or {}
        return entry_func(**args) if isinstance(args, dict) else entry_func(args)