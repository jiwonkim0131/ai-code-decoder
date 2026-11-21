"""LLM이 내는 구조화 출력(output_type). API 계약(server/models.py)과 따로 둔다.

모델의 말은 이 모양으로 받은 뒤 코드가 검증·보정해서 API 응답 모델로 옮긴다. 그래서 여기에는
URL 필드가 없고(공식 문서 URL은 도구 결과에서만 복사), is_decision처럼 서버가 정하는 값도 없다.
OpenAI strict 스키마에 맞게 기본값을 두지 않는다.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from server.models import BadgeKind, ConditionStatus, OptionId


class _Draft(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- 분석(Analyst) ------------------------------------------------------


class DraftEvidence(_Draft):
    badge: BadgeKind = Field(
        description="official은 lookup_docs가 exact_match를 준 심볼만. 코드에서 유추하면 "
        "inferred, 코드만으로 알 수 없으면 needs_check"
    )
    symbol: str | None = Field(description="주장이 가리키는 심볼(예: asyncio.gather)")
    note: str = Field(description="inferred면 추정한 이유, needs_check면 무엇을 확인해야 하는지")


class DraftIO(_Draft):
    in_: str = Field(alias="in", description="이 단계가 받는 값")
    out: str = Field(description="이 단계가 내는 값")


class DraftStep(_Draft):
    id: int = Field(description="1부터 차례로")
    title: str = Field(description="단계 이름(짧게)")
    symbol: str = Field(description="이 단계의 핵심 심볼. 코드에 실제로 적힌 이름")
    lines: list[int] = Field(description="이 단계가 가리키는 줄 번호들(입력 줄 번호 기준)")
    summary: str = Field(description="이 단계가 하는 일 1~2문장")
    io: DraftIO | None
    evidence: list[DraftEvidence] = Field(description="이 단계의 사실 주장마다 근거 하나")


class DraftOption(_Draft):
    id: OptionId
    label: str = Field(description="선택지 이름(예: asyncio.gather)")
    detail: str = Field(description="이 선택지가 어떻게 동작하는지 한 문장")


class DraftCondition(_Draft):
    id: str = Field(description="결정 지점 안에서 고유한 ID(c1, c2, ...)")
    label: str = Field(description="결정을 가른 조건 이름(예: 실패한 tool의 결과를 버려도 되는가)")
    explanation: str = Field(description="이 조건이 왜 선택을 가르는지")
    splits: list[OptionId] = Field(description="이 조건에서 유리해지는 선택지들")


class DraftDecisionPoint(_Draft):
    id: str = Field(description="dp1, dp2, ...")
    step_id: int = Field(description="이 결정이 일어나는 단계 id")
    lines: list[int]
    prompt: str = Field(
        description='"~하려면 어떤 방식이 적절할까요?" 형태의 설계 선택 질문. '
        "정답을 암시하지 않는다"
    )
    options: list[DraftOption] = Field(description="A~D 4개. 하나는 이 코드의 선택")
    code_option: OptionId = Field(description="이 코드가 실제로 택한 선택지")
    hint: str = Field(description="정답을 말하지 않고 생각할 방향만 주는 힌트")
    conditions: list[DraftCondition] = Field(description="결정을 가른 조건 2~4개")


class DraftDecisionCandidate(_Draft):
    topic: str = Field(description="무엇을 고른 결정인지(예: Semaphore로 동시 실행 수 제한)")
    lines: list[int] = Field(description="그 결정이 드러나는 줄 번호들")


class AnalysisDraft(_Draft):
    steps: list[DraftStep]
    # decision_points보다 먼저 쓰게 해서 후보를 다 본 뒤 고르게 한다. 개수는 서버가 맞춰 본다.
    decision_candidates: list[DraftDecisionCandidate] = Field(
        description="결정 지점을 쓰기 전에, 다른 방식도 가능했던 설계 결정 후보를 "
        "줄·관점별로 모두 나열"
    )
    decision_points: list[DraftDecisionPoint]


# --- 판정(Judge) --------------------------------------------------------


class DraftConditionVerdict(_Draft):
    id: str = Field(description="입력 조건의 id 그대로")
    quote: str = Field(
        description="<answer>에서 이 조건을 다룬 부분을 고치지 않고 그대로 옮긴 것. "
        "결론이 이 코드와 반대여도 조건을 따졌으면 옮긴다. 없으면 빈 문자열"
    )
    status: ConditionStatus | None = Field(
        description="hit(답이 이 조건을 짚음) 또는 miss. 건너뛰기(설명 모드)면 null"
    )
    note: str = Field(description="왜 짚음·놓침인지. 설명 모드면 이 조건이 왜 중요한지")


class DraftCodeChoice(_Draft):
    summary: str = Field(description="이 코드가 그 선택지를 고른 이유를 조건으로 설명")
    intent_note: str = Field(description="작성자 의도를 무엇에서 추정했는지 한 문장")


class DraftTradeoffRow(_Draft):
    """방식 이름과 "이 코드" 표시는 서버가 선택지와 code_option으로 채운다."""

    option_id: OptionId = Field(description="이 행이 설명하는 선택지 id")
    wait_time: str = Field(description="기다리는 시간")
    on_failure: str = Field(description="하나가 실패했을 때의 동작")
    complexity: str = Field(description="구현·운영 복잡도")


class JudgeDraft(_Draft):
    conditions: list[DraftConditionVerdict]
    code_choice: DraftCodeChoice
    tradeoffs: list[DraftTradeoffRow] = Field(description="선택지마다 한 행")


# --- 코치(Coach) --------------------------------------------------------


class ScenarioDraft(_Draft):
    question: str = Field(description="실패 주입 질문. 물음표는 끝에 하나만")
    follow_up: str = Field(description="질문 아래 짧은 안내 한 문장(60자 이내). 어디를 볼지만")
    hint: str = Field(
        description="안내보다 구체적인 힌트 1~2문장. 볼 줄·호출과 비교할 것만 쓰고 "
        "결과·정답은 쓰지 않음"
    )


class ModelAnswerDraft(_Draft):
    answer: str = Field(description="질문의 모범 답변 3~5문장. 이 코드의 줄·호출을 들어 설명")


class DraftTakeaway(_Draft):
    text: str = Field(description="배운 점 한 문장. 이 코드의 심볼·줄을 들어 구체적으로")
    refs: list[str] = Field(
        description='근거가 된 기록의 ref 1개 이상. 입력에 적힌 ref(예: "step:2", '
        '"decision:dp1", "apply:latency")만'
    )


class SummaryDraft(_Draft):
    takeaways: list[DraftTakeaway] = Field(description="핵심 정리 3~5개. 중요한 순서로")
    review_points: list[str] = Field(
        description="다시 볼 것 0~3개. 미반영 판단 기준이나 피드백의 아쉬운 점에서만"
    )


class FeedbackDraft(_Draft):
    verdict: str = Field(description="답 전체에 대한 한 줄 평")
    points: list[str] = Field(description="잘한 점 0~3개. 답에 실제로 있는 내용만")
    further: str = Field(description='"더 생각해 볼 것" 1~2문장')
