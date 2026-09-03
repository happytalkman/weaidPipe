"""Unit tests for Phase 1: CodeAct Autonomous Engine and ToolCrafter."""
import os
import tempfile
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from core.codeact_engine import CodeActEngine, CodeActResult
from actions.toolcraft import ToolCrafter, CustomTool


@pytest.fixture
def temp_tools_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


def test_codeact_basic_execution():
    engine = CodeActEngine()
    code = """
x = 10
y = 25
print(f'SUM:{x + y}')
"""
    result = engine.execute_code(code, timeout_s=10.0)
    assert result.success is True
    assert 'SUM:35' in result.output
    assert result.execution_time >= 0


def test_codeact_syntax_error():
    engine = CodeActEngine()
    code = "def invalid_syntax(:\n    return 42"
    result = engine.execute_code(code)
    assert result.success is False
    assert 'SyntaxError' in result.error


def test_codeact_runtime_error():
    engine = CodeActEngine()
    code = "print(10 / 0)"
    result = engine.execute_code(code)
    assert result.success is False
    assert 'ZeroDivisionError' in result.error


def test_codeact_timeout():
    engine = CodeActEngine()
    code = """
import time
time.sleep(5)
print('Done')
"""
    result = engine.execute_code(code, timeout_s=1.0)
    assert result.success is False
    assert 'timed out' in result.error.lower()


def test_codeact_self_repair_loop():
    engine = CodeActEngine()
    with patch('core.llm.generate') as mock_gen:
        mock_gen.side_effect = [
            'print(undefined_variable)',
            'x = 100\nprint(f"REPAIRED:{x}")',
        ]
        result = engine.run_codeact(goal='Print 100', max_retries=2)
        assert result.success is True
        assert 'REPAIRED:100' in result.output
        assert result.attempts == 2


def test_toolcraft_register_and_execute(temp_tools_dir):
    crafter = ToolCrafter(tools_dir=temp_tools_dir)
    code = """
def run(text: str, repeat: int = 2) -> str:
    return text * repeat
"""
    tool = crafter.register_tool(
        name='text_repeater',
        description='Repeats given text n times',
        parameters={'text': 'string', 'repeat': 'integer'},
        code=code,
        tags=['string', 'utility']
    )
    assert tool.name == 'text_repeater'
    assert (temp_tools_dir / 'text_repeater.py').exists()
    assert (temp_tools_dir / 'registry.json').exists()

    res = crafter.execute_tool('text_repeater', {'text': 'AID', 'repeat': 3})
    assert res == 'AIDAIDAID'
    assert crafter.get_tool('text_repeater').usage_count == 1


def test_toolcraft_persistence(temp_tools_dir):
    crafter1 = ToolCrafter(tools_dir=temp_tools_dir)
    crafter1.register_tool(
        name='adder',
        description='Adds two numbers',
        parameters={'a': 'number', 'b': 'number'},
        code='def run(a, b): return a + b'
    )
    crafter2 = ToolCrafter(tools_dir=temp_tools_dir)
    tool = crafter2.get_tool('adder')
    assert tool is not None
    assert tool.description == 'Adds two numbers'
    res = crafter2.execute_tool('adder', {'a': 15, 'b': 25})
    assert res == 40


def test_toolcraft_invalid_name(temp_tools_dir):
    crafter = ToolCrafter(tools_dir=temp_tools_dir)
    with pytest.raises(ValueError):
        crafter.register_tool(
            name='   ',
            description='Empty',
            parameters={},
            code='def run(): pass'
        )

