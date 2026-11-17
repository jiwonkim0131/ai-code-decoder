"""골든 샘플의 케이스 모델과 응답 검사(BE-10).

스터디 레포 `evals/week6`의 데이터셋 구조(jsonl 한 줄 = 케이스 하나, strict 모델)만 가져왔다.
검사는 LLM 채점이 아니라 응답 JSON에 대한 결정적 규칙이다. 서버의 verify.py가 이미 막는 것
(줄 범위, 심볼, 참조)은 응답이 200이면 통과한 것이고, 여기서는 기대 단계 수·결정 지점·배지·
판정 방향처럼 "품질"에 가까운 것을 본다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from server.models import AudienceLevel, OptionId, Preset

GOLDEN_DIR = Path(__file__).resolve().parent
CASES_FILE = GOLDEN_DIR / "cases.jsonl"
SAMPLES_DIR = GOLDEN_DIR / "samples"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AnalyzeExpect(_Strict):
    steps: tuple[int, int]
    min_decision_points: int = 0
    decision_lines: list[int] = Field(default_factory=list)  # 이 줄 중 하나를 가리키는 결정 지점
    official_symbols: list[str] = Field(default_factory=list)  # 공식 문서 배지가 붙어야 할 심볼


class JudgeCase(_Strict):
    label: str
    choice: OptionId | None
    reason: str
    min_hit: int | None = None
    max_hit: int | None = None


class ScenarioCase(_Strict):
    preset: Preset
    keywords: list[str]  # 질문에 이 중 하나는 들어가야 한다
    answer: str
    min_points: int = 0


class GoldenCase(_Strict):
    case_id: str
    sample: str
    level: AudienceLevel
    focus_lines: list[int]
    question: str
    expect: AnalyzeExpect
    judge: list[JudgeCase]
    scenarios: list[ScenarioCase]

    @property
    def code(self) -> str:
        return (SAMPLES_DIR / self.sample).read_text(encoding="utf-8")


def load_cases(path: Path = CASES_FILE) -> list[GoldenCase]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [GoldenCase.model_validate(json.loads(line)) for line in lines if line.strip()]


Failures = list[str]


def check_analyze(body: dict[str, Any], expect: AnalyzeExpect) -> Failures:
    failures: Failures = []
    steps = body["steps"]
    low, high = expect.steps
    if not low <= len(steps) <= high:
        failures.append(f"단계 수 {len(steps)}개(기대 {low}~{high})")
    dps = body["decisionPoints"]
    if len(dps) < expect.min_decision_points:
        failures.append(f"결정 지점 {len(dps)}개(기대 {expect.min_decision_points}개 이상)")
    if expect.decision_lines and not any(
        set(dp["lines"]) & set(expect.decision_lines) for dp in dps
    ):
        failures.append(f"{expect.decision_lines}번째 줄을 가리키는 결정 지점이 없음")
    official = {
        evidence["symbol"]
        for step in steps
        for evidence in step["evidence"]
        if evidence["badge"] == "official"
    }
    for symbol in expect.official_symbols:
        if not any(name == symbol or name.endswith(f".{symbol}") for name in official if name):
            failures.append(f"{symbol}에 공식 문서 배지 없음(official: {sorted(official)})")
    for step in steps:
        if not step["evidence"]:
            failures.append(f"{step['id']}단계에 근거 배지 없음")
        for evidence in step["evidence"]:
            if evidence["badge"] == "official" and not evidence.get("url"):
                failures.append(f"{step['id']}단계 official 배지에 URL 없음")
    for dp in dps:
        condition_ids = [condition["id"] for condition in dp["conditions"]]
        if len(condition_ids) < 2:
            failures.append(f"{dp['id']}의 조건이 {len(condition_ids)}개(2개 이상 권장)")
    return failures


def check_judge(body: dict[str, Any], case: JudgeCase) -> Failures:
    failures: Failures = []
    conditions = body["conditions"]
    if body["total"] != len(conditions):
        failures.append("total과 조건 수가 다름")
    if case.choice is None:
        if not body["skipped"] or body["hit"] is not None:
            failures.append("건너뛰기인데 채점됨")
    else:
        hit = body["hit"]
        if case.min_hit is not None and hit < case.min_hit:
            failures.append(f"짚음 {hit}개(기대 {case.min_hit}개 이상)")
        if case.max_hit is not None and hit > case.max_hit:
            failures.append(f"짚음 {hit}개(기대 {case.max_hit}개 이하)")
    rows = [row for row in body["tradeoffs"]["rows"] if row["isThisCode"]]
    if len(rows) != 1 or rows[0]["approach"] != body["codeChoice"]["label"]:
        failures.append("트레이드오프 표의 '이 코드' 행이 codeChoice와 다름")
    return failures


def check_scenario(body: dict[str, Any], case: ScenarioCase) -> Failures:
    failures: Failures = []
    question = body["question"]
    if question.count("?") != 1:
        failures.append(f"질문의 물음표 {question.count('?')}개")
    if not any(keyword in question for keyword in case.keywords):
        failures.append(f"질문에 프리셋 단서({', '.join(case.keywords)})가 없음")
    return failures


def check_feedback(body: dict[str, Any], case: ScenarioCase) -> Failures:
    if len(body["points"]) < case.min_points:
        return [f"잘한 점 {len(body['points'])}개(기대 {case.min_points}개 이상)"]
    return []
