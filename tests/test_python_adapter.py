from __future__ import annotations

import pytest

from server.languages import LanguageUnsupportedError, get_adapter
from server.languages.base import CodeAnalysisError, CodeSyntaxError
from server.languages.python import PythonAdapter
from server.models import MAX_CODE_CHARACTERS

# 목업 사례(agent_tools.py)와 같은 모양의 코드
AGENT_TOOLS = """\
import asyncio

from tools import plan_tools, merge_results


async def run_tools(question):
    calls = plan_tools(question)
    tasks = [call_tool(name, args) for name, args in calls]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    return merge_results(results)


async def call_tool(name, args):
    await asyncio.sleep(0)
    return {"name": name, "args": args}
"""


@pytest.fixture
def adapter() -> PythonAdapter:
    return PythonAdapter()


def _call(facts, name):
    return next(call for call in facts.calls if call.name == name)


def test_extracts_imports_symbols_calls_and_line_ranges(adapter):
    facts = adapter.extract_facts(AGENT_TOOLS)

    assert facts.language == "python"
    assert facts.line_count == 15
    assert [(i.module, i.name, i.line) for i in facts.imports] == [
        ("asyncio", None, 1),
        ("tools", "plan_tools", 3),
        ("tools", "merge_results", 3),
    ]
    assert [(s.name, s.kind, s.line, s.end_line) for s in facts.symbols] == [
        ("run_tools", "async_function", 6, 10),
        ("call_tool", "async_function", 13, 15),
    ]

    gather = _call(facts, "asyncio.gather")
    assert (gather.module, gather.symbol, gather.resolved) == ("asyncio", "gather", True)
    assert gather.lines == [9]
    assert gather.expression.startswith("asyncio.gather(*tasks")
    assert _call(facts, "merge_results").module == "tools"
    assert _call(facts, "merge_results").used_as_input_of is None
    assert set(facts.syntax) == {"async/await", "comprehension", "unpacking with *"}


def test_same_call_on_several_lines_is_merged_with_earliest_expression(adapter):
    facts = adapter.extract_facts(
        "import json\n\n\ndef f(x):\n    return json.dumps(x)\n\n\njson.dumps(1)\n"
    )
    dumps = _call(facts, "json.dumps")
    assert dumps.lines == [5, 8]
    assert dumps.expression == "json.dumps(x)"
    assert [c.lines[0] for c in facts.calls] == sorted(c.lines[0] for c in facts.calls)


def test_nested_definitions_are_qualified(adapter):
    facts = adapter.extract_facts(
        "class A:\n    def m(self):\n        def inner():\n            pass\n"
    )
    assert [s.name for s in facts.symbols] == ["A", "A.m", "A.m.inner"]


def test_has_symbol_only_accepts_resolved_calls_in_facts(adapter):
    facts = adapter.extract_facts(AGENT_TOOLS)
    assert facts.has_symbol("asyncio", "gather")
    assert not facts.has_symbol("asyncio", "TaskGroup")  # 코드에 없는 심볼
    assert not facts.has_symbol("", "call_tool")  # 해석되지 않은 호출


def test_syntax_error_reports_line(adapter):
    with pytest.raises(CodeSyntaxError) as exc_info:
        adapter.extract_facts("x = 1\ndef broken(:\n    pass\n")
    assert exc_info.value.line == 2
    assert "Python 코드만" in str(exc_info.value)


def test_other_language_is_a_syntax_error(adapter):
    with pytest.raises(CodeSyntaxError):
        adapter.extract_facts("const x = () => { return 1; };")


def test_null_byte_is_a_syntax_error(adapter):
    with pytest.raises(CodeSyntaxError):
        adapter.extract_facts("x = 1\x00")


def test_code_over_limit_is_rejected(adapter):
    with pytest.raises(CodeAnalysisError):
        adapter.extract_facts("#" * (MAX_CODE_CHARACTERS + 1))


def test_code_is_never_executed(adapter, tmp_path):
    marker = tmp_path / "executed"
    adapter.extract_facts(f"open({str(marker)!r}, 'w').write('x')\nraise SystemExit(1)\n")
    assert not marker.exists()


def test_prompt_hints_follow_level(adapter):
    assert "입문" in adapter.prompt_hints("basic")
    assert "심화" in adapter.prompt_hints("deep")


# --- 레포 analyzer의 Path 해석 규칙(이식) -------------------------------


@pytest.mark.parametrize(
    "prefix,call",
    [
        ("from pathlib import Path", "Path"),
        ("from pathlib import Path as P", "P"),
        ("import pathlib as p", "p.Path"),
    ],
)
def test_inline_path_method_is_resolved(adapter, prefix, call):
    facts = adapter.extract_facts(f'{prefix}\nfiles = sorted({call}("images").glob("*.png"))')
    target = next(c for c in facts.calls if c.symbol == "Path.glob")
    assert target.module == "pathlib"
    assert target.resolved
    assert target.used_as_input_of == "sorted"


def test_path_variable_method_is_resolved(adapter):
    facts = adapter.extract_facts('from pathlib import Path\nD = Path("x")\nD.glob("*")')
    assert facts.has_symbol("pathlib", "Path.glob")


def test_unknown_constructor_is_not_claimed_as_pathlib(adapter):
    facts = adapter.extract_facts('from private_lib import Path\nPath("x").glob("*")')
    assert not any(c.module == "pathlib" for c in facts.calls)


@pytest.mark.parametrize(
    "binding", ["Path = factory", 'def f(Path):\n    return Path("x").glob("*")']
)
def test_rebound_path_constructor_is_not_resolved(adapter, binding):
    facts = adapter.extract_facts("from pathlib import Path\n" + binding + '\nPath("x").glob("*")')
    assert not facts.has_symbol("pathlib", "Path.glob")


# --- 어댑터 등록표 -------------------------------------------------------


def test_get_adapter_respects_allowed_langs(monkeypatch):
    assert get_adapter("Python").id == "python"
    with pytest.raises(LanguageUnsupportedError):
        get_adapter("javascript")

    from server.config import get_settings

    monkeypatch.setenv("ALLOWED_LANGS", "javascript")
    get_settings.cache_clear()
    with pytest.raises(LanguageUnsupportedError) as exc_info:
        get_adapter("python")
    assert exc_info.value.supported == []


@pytest.mark.parametrize(
    "code,expected",
    [
        # 외부 호출·동시성·대기가 있는 줄만 남는다(모르는 패키지도 외부 호출로 본다).
        (
            "import asyncio\nimport tools\nasync def f(xs):\n    await asyncio.gather(*xs)\n"
            "    return tools.call(len(xs))\n",
            {4, 5},
        ),
        ("import requests, time\nr = requests.get('u', timeout=5)\ntime.sleep(1)\n", {2, 3}),
        # 계산·변환·설정 읽기뿐이면 비어 있다(설계 응용을 건너뛴다).
        ("def total(items):\n    return round(sum(items), 2)\n", set()),
        ("import json\nwith open('c.json') as f:\n    c = json.load(f)\n", set()),
    ],
)
def test_operational_lines(adapter, code, expected):
    assert adapter.operational_lines(adapter.extract_facts(code)) == expected
