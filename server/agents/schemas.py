"""LLM이 내는 구조화 출력(output_type). API 계약(server/models.py)과 따로 둔다.

모델의 말은 이 모양으로 받은 뒤 코드가 검증·보정해서 API 응답 모델로 옮긴다. 그래서 여기에는
URL 필드가 없고(공식 문서 URL은 도구 결과에서만 복사), is_decision처럼 서버가 정하는 값도 없다.
OpenAI strict 스키마에 맞게 기본값을 두지 않는다.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from server.models import BadgeKind, OptionId


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
    prompt: str = Field(description='"너라면?" 질문. 정답을 암시하지 않는다')
    options: list[DraftOption] = Field(description="A~D 4개. 하나는 이 코드의 선택")
    code_option: OptionId = Field(description="이 코드가 실제로 택한 선택지")
    hint: str = Field(description="정답을 말하지 않고 생각할 방향만 주는 힌트")
    conditions: list[DraftCondition] = Field(description="결정을 가른 조건 2~4개")


class AnalysisDraft(_Draft):
    steps: list[DraftStep]
    decision_points: list[DraftDecisionPoint]
