"""API 요청·응답 계약(BE-02).

JSON 필드는 camelCase, 파이썬 속성은 snake_case다. FastAPI가 이 모델로 OpenAPI를 만들고,
FE는 그 OpenAPI에서 타입을 생성한다(`scripts/export_openapi.py`).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

# 스터디 레포의 MAX_CODE_CHARACTERS와 같은 값
MAX_CODE_CHARACTERS = 20_000
MAX_QUESTION_CHARACTERS = 1_000
MAX_ANSWER_CHARACTERS = 2_000
# FE가 다시 보내 프롬프트에 들어가는 목록의 상한(비용·남용 보호, BE-09)
MAX_STEPS = 8
MAX_EVIDENCE = 20

AudienceLevel = Literal["basic", "work", "deep"]
OptionId = Literal["A", "B", "C", "D"]
BadgeKind = Literal["official", "inferred", "needs_check"]
Preset = Literal["latency", "rateLimit", "traffic", "team"]
ConditionStatus = Literal["hit", "miss"]

LineNumber = Annotated[int, Field(ge=1)]
Code = Annotated[str, Field(min_length=1, max_length=MAX_CODE_CHARACTERS)]


class ApiModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )


# --- 공통 ---------------------------------------------------------------


class Evidence(ApiModel):
    """사실 주장 하나에 붙는 근거 배지. official이면 url이 반드시 있다."""

    badge: BadgeKind
    symbol: str | None = None
    url: str | None = None
    note: str | None = None

    @model_validator(mode="after")
    def _official_needs_url(self) -> Evidence:
        if self.badge == "official" and not self.url:
            raise ValueError("official 배지에는 url이 필요합니다.")
        return self


class StepIO(ApiModel):
    in_: str = Field(alias="in")
    out: str


class Step(ApiModel):
    """흐름 설명 카드 1장. 줄 범위는 다른 단계와 겹칠 수 있다."""

    id: int = Field(ge=1)
    title: str
    symbol: str
    lines: list[LineNumber] = Field(min_length=1)
    summary: str
    io: StepIO | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    is_decision: bool = False


class Option(ApiModel):
    id: OptionId
    label: str
    detail: str | None = None


class Condition(ApiModel):
    """결정을 가른 조건. 분석 때 한 번 만들어 고정하며 제출 전에는 화면에 보이지 않는다."""

    id: str
    label: str
    explanation: str
    splits: list[OptionId] = Field(default_factory=list)


class DecisionPoint(ApiModel):
    """결정 지점 1개 = "너라면?" 1문제."""

    id: str
    step_id: int = Field(ge=1)
    lines: list[LineNumber] = Field(min_length=1)
    prompt: str
    options: list[Option] = Field(min_length=4, max_length=4)
    code_option: OptionId = Field(
        description="이 코드가 실제로 택한 선택지. 판정 화면의 '이 코드의 선택'"
    )
    hint: str
    conditions: list[Condition] = Field(min_length=1)


# --- POST /api/scan -----------------------------------------------------


class ScanRequest(ApiModel):
    code: Code


class SecretFinding(ApiModel):
    """비밀값 후보. start·end는 그 줄 안의 문자 위치(0부터, end는 미포함)다."""

    line: LineNumber
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    kind: str
    preview: str


class ScanResponse(ApiModel):
    findings: list[SecretFinding]


# --- POST /api/analyze --------------------------------------------------


class AnalyzeRequest(ApiModel):
    code: Code
    level: AudienceLevel = "work"
    focus_lines: list[LineNumber] = Field(default_factory=list)
    question: str = Field(default="", max_length=MAX_QUESTION_CHARACTERS)
    language: str = "python"


class ToolCall(ApiModel):
    """에이전트가 부른 도구 이름과 인자. 코드 원문은 담지 않는다."""

    name: str
    args: dict[str, str] | None = None


class AnalyzeMeta(ApiModel):
    model: str
    tool_calls: list[ToolCall] = Field(default_factory=list)


class AnalyzeResponse(ApiModel):
    language: str
    steps: list[Step]
    decision_points: list[DecisionPoint]
    meta: AnalyzeMeta


# --- POST /api/judge ----------------------------------------------------


class JudgeRequest(ApiModel):
    decision_point: DecisionPoint
    evidence: list[Evidence] = Field(default_factory=list, max_length=MAX_EVIDENCE)
    choice: OptionId | None = None
    reason: str = Field(default="", max_length=MAX_ANSWER_CHARACTERS)
    level: AudienceLevel = "work"


class ConditionVerdict(ApiModel):
    id: str
    status: ConditionStatus | None = None
    note: str


class MyAnswer(ApiModel):
    choice: OptionId
    reason: str


class CodeChoice(ApiModel):
    """이 코드의 선택. 작성자 의도는 항상 "추정" 배지다."""

    label: str
    summary: str
    intent: Evidence


class TradeoffRow(ApiModel):
    approach: str
    wait_time: str
    on_failure: str
    complexity: str
    is_this_code: bool = False


class Tradeoffs(ApiModel):
    rows: list[TradeoffRow] = Field(min_length=1)
    evidence: Evidence


class Verdict(ApiModel):
    """판정 결과. skipped면 hit·mine이 null이고 조건 status도 비어 있다(설명 모드)."""

    skipped: bool
    hit: int | None = None
    total: int = Field(ge=0)
    conditions: list[ConditionVerdict]
    mine: MyAnswer | None = None
    code_choice: CodeChoice
    tradeoffs: Tradeoffs


# --- POST /api/scenario, /api/feedback ---------------------------------


class ScenarioRequest(ApiModel):
    code: Code
    preset: Preset
    steps: list[Step] = Field(default_factory=list, max_length=MAX_STEPS)


class ScenarioResponse(ApiModel):
    preset: Preset
    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARACTERS)
    follow_up: str


class FeedbackRequest(ApiModel):
    code: Code
    preset: Preset
    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARACTERS)
    answer: str = Field(min_length=1, max_length=MAX_ANSWER_CHARACTERS)


class FeedbackResponse(ApiModel):
    verdict: str
    points: list[str]
    further: str


# --- 오류 ---------------------------------------------------------------


class ErrorBody(ApiModel):
    code: str
    message: str
    details: dict[str, Any] | None = None


class ErrorResponse(ApiModel):
    error: ErrorBody
