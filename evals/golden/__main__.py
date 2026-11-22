"""골든 샘플 평가: `python -m evals.golden [--case ID] [--save]`.

실제 OpenAI를 부르므로 비용이 든다(.env의 OPENAI_API_KEY). 앱을 프로세스 안에서 띄워 FE와 같은
경로(/api/analyze → judge → constraints → scenario → feedback)로 부르고, checks.py의 규칙으로
확인한다.
`--save`를 주면 응답을 evals/golden/out/<case>/에 저장한다(FE 개발용 fixture로 쓸 수 있다).
실패한 검사가 하나라도 있으면 종료 코드 1이다.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from evals.golden.checks import (
    GOLDEN_DIR,
    GoldenCase,
    check_analyze,
    check_constraints,
    check_feedback,
    check_judge,
    check_scenario,
    find_constraint,
    load_cases,
)

OUT_DIR = GOLDEN_DIR / "out"


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, float, list[str]]] = []

    def add(self, case_id: str, name: str, seconds: float, failures: list[str]) -> None:
        self.rows.append((case_id, name, seconds, failures))
        mark = "PASS" if not failures else "FAIL"
        print(f"{mark}  {case_id:<18} {name:<28} {seconds:5.1f}s")
        for failure in failures:
            print(f"      - {failure}")

    @property
    def failed(self) -> int:
        return sum(1 for *_, failures in self.rows if failures)


def _post(client, path: str, body: dict[str, Any]) -> tuple[int, dict[str, Any], float]:
    started = time.perf_counter()
    response = client.post(path, json=body)
    return response.status_code, response.json(), time.perf_counter() - started


def _save(case_id: str, name: str, body: dict[str, Any]) -> None:
    folder = OUT_DIR / case_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.json").write_text(
        json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def run_case(client, case: GoldenCase, report: Report, save: bool) -> None:
    code = case.code
    request = {
        "code": code,
        "level": case.level,
        "focusLines": case.focus_lines,
        "question": case.question,
    }
    status, analysis, seconds = _post(client, "/api/analyze", request)
    if status != 200:
        report.add(case.case_id, "analyze", seconds, [f"HTTP {status}: {analysis}"])
        return
    report.add(case.case_id, "analyze", seconds, check_analyze(analysis, case.expect))
    if save:
        _save(case.case_id, "analyze", {"request": request, "response": analysis})

    dps = analysis["decisionPoints"]
    for index, judge_case in enumerate(case.judge):
        name = f"judge: {judge_case.label}"
        if not dps:
            report.add(case.case_id, name, 0.0, ["결정 지점이 없어 판정할 수 없음"])
            continue
        dp = dps[0]
        evidence = next(s["evidence"] for s in analysis["steps"] if s["id"] == dp["stepId"])
        body = {
            "decisionPoint": dp,
            "evidence": evidence,
            "choice": judge_case.choice,
            "reason": judge_case.reason,
            "level": case.level,
        }
        status, verdict, seconds = _post(client, "/api/judge", body)
        failures = (
            [f"HTTP {status}: {verdict}"] if status != 200 else check_judge(verdict, judge_case)
        )
        report.add(case.case_id, name, seconds, failures)
        if save and status == 200:
            _save(case.case_id, f"judge-{index}", {"request": body, "response": verdict})

    body = {"code": code, "steps": analysis["steps"]}
    status, constraints, seconds = _post(client, "/api/constraints", body)
    if status != 200:
        report.add(case.case_id, "constraints", seconds, [f"HTTP {status}: {constraints}"])
        return
    report.add(case.case_id, "constraints", seconds, check_constraints(constraints, case.expect))
    if save:
        _save(case.case_id, "constraints", {"request": body, "response": constraints})

    for scenario_case in case.scenarios:
        constraint = find_constraint(constraints, scenario_case)
        if constraint is None:
            labels = [c["label"] for c in constraints["constraints"]]
            name = f"scenario: {'/'.join(scenario_case.constraint_keywords)}"
            report.add(case.case_id, name, 0.0, [f"맞는 제약이 없음(제약: {labels})"])
            continue
        cid = constraint["id"]
        body = {"code": code, "constraint": constraint, "steps": analysis["steps"]}
        status, scenario, seconds = _post(client, "/api/scenario", body)
        if status != 200:
            report.add(case.case_id, f"scenario: {cid}", seconds, [f"HTTP {status}: {scenario}"])
            continue
        report.add(
            case.case_id, f"scenario: {cid}", seconds, check_scenario(scenario, scenario_case)
        )
        feedback_body = {
            "code": code,
            "constraint": constraint,
            "question": scenario["question"],
            "answer": scenario_case.answer,
        }
        status, feedback, seconds = _post(client, "/api/feedback", feedback_body)
        failures = (
            [f"HTTP {status}: {feedback}"]
            if status != 200
            else check_feedback(feedback, scenario_case)
        )
        report.add(case.case_id, f"feedback: {cid}", seconds, failures)
        if save:
            _save(case.case_id, f"scenario-{cid}", {"request": body, "response": scenario})
            if status == 200:
                _save(
                    case.case_id,
                    f"feedback-{cid}",
                    {"request": feedback_body, "response": feedback},
                )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.golden")
    parser.add_argument("--case", action="append", help="이 케이스만 실행(여러 번 줄 수 있음)")
    parser.add_argument("--save", action="store_true", help="응답을 evals/golden/out/에 저장")
    args = parser.parse_args(argv)

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from fastapi.testclient import TestClient

    from app import app
    from server.config import get_settings

    if not get_settings().has_openai_key:
        print("OPENAI_API_KEY가 없어 골든 평가를 실행할 수 없습니다(.env를 확인하세요).")
        return 2

    cases = [c for c in load_cases() if not args.case or c.case_id in args.case]
    report = Report()
    # with 블록으로 이벤트 루프 하나를 유지한다(OpenAI 클라이언트의 연결 풀이 루프에 묶인다).
    with TestClient(app) as client:
        for case in cases:
            run_case(client, case, report, args.save)
    print(f"\n{len(report.rows) - report.failed}/{len(report.rows)} passed")
    return 1 if report.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
