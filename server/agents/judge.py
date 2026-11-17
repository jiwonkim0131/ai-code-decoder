"""판정 `POST /api/judge`(BE-07).

도구 없는 구조화 LLM 호출이다(Judge, Agent + output_type). 서버는 기억하는 것이 없어 결정 지점과
조건을 요청에서 받고, 조건은 데이터로만 다룬다. 출력의 조건 ID가 입력 조건과 정확히 맞는지
(짚음 + 놓침 = 전체) 코드로 검증한다. choice가 null이면 채점 없는 설명 모드다.
트레이드오프 표의 방식 이름과 "이 코드" 표시는 모델이 아니라 선택지와 codeOption으로 서버가 채운다.
표 배지는 판정 에이전트가 문서를 조회하지 않으므로 요청에 실린 단계 evidence의
official URL(허용 도메인만)을 복사하고, 없으면 "추정"이다.
"""

from __future__ import annotations

from agents import Agent, ModelSettings

from server.agents import runtime
from server.agents.prompts import JUDGE_PROMPT, judge_input
from server.agents.runtime import RunStats
from server.agents.schemas import JudgeDraft
from server.config import get_settings
from server.docs.package_registry import is_official_docs_url
from server.errors import ApiError, ErrorCode
from server.models import (
    CodeChoice,
    ConditionVerdict,
    Evidence,
    JudgeRequest,
    MyAnswer,
    TradeoffRow,
    Tradeoffs,
    Verdict,
)
from server.verify import VerificationError

MAX_TURNS = 1  # 도구가 없어 최종 출력 한 번
MAX_OUTPUT_TOKENS = 2000
INTENT_NOTE = "작성자 의도는 코드에서 추정한 내용이에요."
TRADEOFF_INFERRED_NOTE = "공식 문서로 확인한 내용이 없어 일반적인 동작을 바탕으로 정리했어요."


def build_judge() -> Agent[None]:
    return Agent(
        name="Judge",
        instructions=JUDGE_PROMPT,
        model=runtime.agent_model(get_settings()),
        model_settings=ModelSettings(max_tokens=MAX_OUTPUT_TOKENS),
        output_type=JudgeDraft,
    )


def tradeoff_evidence(evidence: list[Evidence]) -> Evidence:
    official = next(
        (
            item
            for item in evidence
            if item.badge == "official" and item.url and is_official_docs_url(item.url)
        ),
        None,
    )
    if official is None:
        return Evidence(badge="inferred", note=TRADEOFF_INFERRED_NOTE)
    return Evidence(badge="official", symbol=official.symbol, url=official.url)


def verify_verdict(draft: JudgeDraft, req: JudgeRequest) -> Verdict:
    dp = req.decision_point
    skipped = req.choice is None
    expected = [condition.id for condition in dp.conditions]
    got = [condition.id for condition in draft.conditions]
    errors: list[str] = []
    if sorted(got) != sorted(expected):
        errors.append(f"conditions의 id는 입력 조건 {expected}와 정확히 같아야 합니다(현재 {got}).")
    if not skipped:
        missing = [c.id for c in draft.conditions if c.status is None]
        if missing:
            errors.append(f"조건 {missing}의 status가 비어 있습니다. hit 또는 miss로 판정하세요.")
    option_ids = sorted(option.id for option in dp.options)
    row_ids = sorted(row.option_id for row in draft.tradeoffs)
    if row_ids != option_ids:
        errors.append(
            f"tradeoffs는 선택지 {option_ids}마다 정확히 한 행이어야 합니다(현재 {row_ids})."
        )
    if errors:
        raise VerificationError(errors)

    by_id = {condition.id: condition for condition in draft.conditions}
    conditions = [
        ConditionVerdict(
            id=condition_id,
            status=None if skipped else by_id[condition_id].status,
            note=by_id[condition_id].note,
        )
        for condition_id in expected
    ]
    labels = {option.id: option.label for option in dp.options}
    return Verdict(
        skipped=skipped,
        hit=None if skipped else sum(1 for c in conditions if c.status == "hit"),
        total=len(conditions),
        conditions=conditions,
        mine=None if skipped else MyAnswer(choice=req.choice, reason=req.reason),
        code_choice=CodeChoice(
            label=labels[dp.code_option],
            summary=draft.code_choice.summary,
            intent=Evidence(
                badge="inferred", note=draft.code_choice.intent_note.strip() or INTENT_NOTE
            ),
        ),
        tradeoffs=Tradeoffs(
            rows=[
                TradeoffRow(
                    approach=labels[row.option_id],
                    wait_time=row.wait_time,
                    on_failure=row.on_failure,
                    complexity=row.complexity,
                    is_this_code=row.option_id == dp.code_option,
                )
                for row in sorted(draft.tradeoffs, key=lambda row: row.option_id)
            ],
            evidence=tradeoff_evidence(req.evidence),
        ),
    )


async def run_judge(req: JudgeRequest, stats: RunStats | None = None) -> Verdict:
    dp = req.decision_point
    if dp.code_option not in {option.id for option in dp.options}:
        raise ApiError(400, ErrorCode.INPUT_INVALID, "결정 지점의 codeOption이 선택지에 없어요.")
    try:
        return await runtime.run_with_correction(
            build_judge(),
            judge_input(dp, req.choice, req.reason, req.level),
            lambda output: verify_verdict(output, req),
            stats=stats or RunStats(),
            max_turns=MAX_TURNS,
            retry_max_turns=MAX_TURNS,
        )
    except VerificationError as exc:
        raise ApiError(
            502, ErrorCode.ANALYSIS_FAILED, "판정에 실패했어요. 다시 시도해 주세요."
        ) from exc
