"""골든 평가 데이터셋과 검사 규칙(BE-10). 실제 평가(`python -m evals.golden`)는 OpenAI를 부른다."""

from __future__ import annotations

import pytest

from evals.golden.checks import (
    AnalyzeExpect,
    JudgeCase,
    ScenarioCase,
    check_analyze,
    check_judge,
    check_scenario,
    load_cases,
)
from server.intake import check_code

GATHER_URL = "https://docs.python.org/3/library/asyncio-task.html#asyncio.gather"


def _analysis(official: bool = True, dp_lines: list[int] | None = None) -> dict:
    badge = (
        {"badge": "official", "symbol": "asyncio.gather", "url": GATHER_URL}
        if official
        else {"badge": "inferred", "symbol": "asyncio.gather", "note": "추정"}
    )
    steps = [
        {"id": i, "evidence": [badge if i == 2 else {"badge": "needs_check", "symbol": "x"}]}
        for i in (1, 2, 3)
    ]
    dp = {"id": "dp1", "lines": dp_lines or [9], "conditions": [{"id": "c1"}, {"id": "c2"}]}
    return {"steps": steps, "decisionPoints": [dp]}


@pytest.mark.parametrize("case", load_cases(), ids=lambda case: case.case_id)
def test_golden_samples_pass_intake(case):
    checked = check_code(case.code)  # 비밀값·문법 오류가 없어야 한다
    assert all(line <= checked.facts.line_count for line in case.focus_lines)


def test_check_analyze_reports_missing_badge_and_decision_line():
    expect = AnalyzeExpect(
        steps=(3, 6), min_decision_points=1, decision_lines=[9], official_symbols=["asyncio.gather"]
    )
    assert check_analyze(_analysis(), expect) == []
    failures = check_analyze(_analysis(official=False, dp_lines=[3]), expect)
    assert any("공식 문서 배지 없음" in f for f in failures)
    assert any("[9]번째 줄" in f for f in failures)


def test_check_judge_and_scenario():
    verdict = {
        "skipped": False,
        "hit": 0,
        "total": 2,
        "conditions": [{}, {}],
        "codeChoice": {"label": "asyncio.gather"},
        "tradeoffs": {"rows": [{"approach": "asyncio.gather", "isThisCode": True}]},
    }
    assert check_judge(verdict, JudgeCase(label="x", choice="A", reason="", max_hit=0)) == []
    assert check_judge(verdict, JudgeCase(label="x", choice="A", reason="", min_hit=1))

    case = ScenarioCase(preset="latency", keywords=["3초"], answer="a")
    assert check_scenario({"question": "3초 걸리면 어떻게 될까요?"}, case) == []
    assert len(check_scenario({"question": "어떻게? 왜?"}, case)) == 2
