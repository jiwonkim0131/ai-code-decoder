"""코치 `POST /api/scenario`, `/api/feedback`, `/api/model-answer`, `/api/summary`(BE-08).

도구 없는 구조화 호출(Coach)이다. 코드를 받으므로 라우트에서 analyze와 같은 입구 검사(허용 언어,
비밀값, 정적 분석)를 거치고, 서버가 사실 목록을 기억하지 않으니 요청마다 다시 만든다.
학습 정리는 FE가 1~5단계 결과를 모아 보내고, 서버는 핵심 정리만 쓴다(기록 자체는 FE가 문서로 조립).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from agents import Agent, ModelSettings

from server.agents import runtime
from server.agents.prompts import (
    COACH_FEEDBACK_PROMPT,
    COACH_MODEL_ANSWER_PROMPT,
    COACH_SCENARIO_PROMPT,
    SUMMARY_PROMPT,
    feedback_input,
    model_answer_input,
    scenario_input,
    summary_input,
)
from server.agents.runtime import RunStats
from server.agents.schemas import FeedbackDraft, ModelAnswerDraft, ScenarioDraft, SummaryDraft
from server.config import get_settings
from server.errors import ApiError, ErrorCode
from server.intake import CheckedCode
from server.models import (
    FeedbackRequest,
    FeedbackResponse,
    ModelAnswerRequest,
    ModelAnswerResponse,
    ScenarioRequest,
    ScenarioResponse,
    SummaryRequest,
    SummaryResponse,
    Takeaway,
)
from server.verify import VerificationError

MAX_TURNS = 1
FOLLOW_UP_MAX_CHARACTERS = 60
HINT_MAX_SENTENCES = 2
MODEL_ANSWER_SENTENCES = (3, 5)
_SENTENCE_END = re.compile(r"[.!?](?=\s|$)")  # 1.5 같은 숫자의 점은 세지 않는다
# 합니다·됩니다·습니다·입니다처럼 '~니다/~니까'로 끝나는 문장(하십시오체)
_FORMAL_ENDING = re.compile(r"니(다|까)[.!?]?(?=\s|$)")
MAX_OUTPUT_TOKENS = 1000
MAX_POINTS = 3
TAKEAWAYS = (3, 5)
MAX_REVIEW_POINTS = 3


def _agent(name: str, instructions: str, output_type: type) -> Agent[None]:
    return Agent(
        name=name,
        instructions=instructions,
        model=runtime.agent_model(get_settings()),
        model_settings=ModelSettings(max_tokens=MAX_OUTPUT_TOKENS),
        output_type=output_type,
    )


def _sentence_count(text: str) -> int:
    text = text.strip()
    return len(_SENTENCE_END.findall(text)) or (1 if text else 0)


def _style_errors(field: str, text: str) -> list[str]:
    if _FORMAL_ENDING.search(text):
        return [f"{field}에 '~습니다' 말투가 있습니다. '~해요' 말투로 바꾸세요."]
    return []


def _style_on_first_try[T](verify: Callable[..., T]) -> Callable[[Any], T]:
    """문장 수·말투는 출력 품질 규칙이라 첫 출력에서만 고쳐 쓰게 하고, 고친 출력은 받는다."""
    attempts = 0

    def run(output: Any) -> T:
        nonlocal attempts
        attempts += 1
        return verify(output, check_style=attempts == 1)

    return run


def verify_scenario(
    draft: ScenarioDraft, preset: str, check_style: bool = True
) -> ScenarioResponse:
    question = draft.question.strip()
    errors: list[str] = []
    if question.count("?") != 1 or not question.endswith("?"):
        errors.append(
            f"question은 물음표가 문장 끝에 하나만 있는 질문 하나여야 합니다"
            f"(현재 물음표 {question.count('?')}개)."
        )
    for field in ("follow_up", "hint"):
        if not getattr(draft, field).strip():
            errors.append(f"{field}이 비어 있습니다.")
    if check_style:
        follow_up, hint = draft.follow_up.strip(), draft.hint.strip()
        if _sentence_count(follow_up) > 1 or len(follow_up) > FOLLOW_UP_MAX_CHARACTERS:
            errors.append(
                f"follow_up은 {FOLLOW_UP_MAX_CHARACTERS}자 이내 한 문장이어야 합니다"
                f"(현재 {_sentence_count(follow_up)}문장, {len(follow_up)}자)."
            )
        if _sentence_count(hint) > HINT_MAX_SENTENCES:
            errors.append(
                f"hint는 {HINT_MAX_SENTENCES}문장 이하여야 합니다"
                f"(현재 {_sentence_count(hint)}문장). "
                "볼 줄·호출과 비교할 것만 남기고 결과·정답은 빼세요."
            )
        errors += _style_errors("follow_up", follow_up) + _style_errors("hint", hint)
    if errors:
        raise VerificationError(errors)
    return ScenarioResponse(
        preset=preset,
        question=question,
        follow_up=draft.follow_up.strip(),
        hint=draft.hint.strip(),
    )


def verify_model_answer(draft: ModelAnswerDraft, check_style: bool = True) -> ModelAnswerResponse:
    answer = draft.answer.strip()
    if not answer:
        raise VerificationError(["answer가 비어 있습니다."])
    errors: list[str] = []
    if check_style:
        low, high = MODEL_ANSWER_SENTENCES
        if not low <= _sentence_count(answer) <= high:
            errors.append(
                f"answer는 {low}~{high}문장이어야 합니다(현재 {_sentence_count(answer)}문장)."
            )
        errors += _style_errors("answer", answer)
    if errors:
        raise VerificationError(errors)
    return ModelAnswerResponse(answer=answer)


def verify_feedback(draft: FeedbackDraft) -> FeedbackResponse:
    points = [point.strip() for point in draft.points if point.strip()]
    errors: list[str] = []
    if len(points) > MAX_POINTS:
        errors.append(f"points는 {MAX_POINTS}개 이하로 고르세요(현재 {len(points)}개).")
    if not draft.verdict.strip():
        errors.append("verdict가 비어 있습니다.")
    if not draft.further.strip():
        errors.append("further가 비어 있습니다.")
    if errors:
        raise VerificationError(errors)
    return FeedbackResponse(
        verdict=draft.verdict.strip(), points=points, further=draft.further.strip()
    )


def summary_refs(req: SummaryRequest) -> set[str]:
    return (
        {f"step:{step.id}" for step in req.steps}
        | {f"decision:{item.decision_point.id}" for item in req.decisions}
        | {f"apply:{item.preset}" for item in req.applications}
    )


def verify_summary(
    draft: SummaryDraft, refs: set[str], check_style: bool = True
) -> SummaryResponse:
    takeaways = [t for t in draft.takeaways if t.text.strip()]
    review_points = [point.strip() for point in draft.review_points if point.strip()]
    errors: list[str] = []
    low, high = TAKEAWAYS
    if not takeaways:
        errors.append("takeaways가 비어 있습니다.")
    for i, takeaway in enumerate(takeaways, 1):
        unknown = [ref for ref in takeaway.refs if ref not in refs]
        if not takeaway.refs or unknown:
            errors.append(
                f"takeaways[{i}]의 refs {unknown or '[]'}는 입력에 없습니다. "
                f"[ref]로 적힌 값({', '.join(sorted(refs))})만 1개 이상 쓰세요."
            )
    if check_style:
        if not low <= len(takeaways) <= high:
            errors.append(f"takeaways는 {low}~{high}개여야 합니다(현재 {len(takeaways)}개).")
        if len(review_points) > MAX_REVIEW_POINTS:
            errors.append(
                f"review_points는 {MAX_REVIEW_POINTS}개 이하로 고르세요"
                f"(현재 {len(review_points)}개)."
            )
        for i, takeaway in enumerate(takeaways, 1):
            errors += _style_errors(f"takeaways[{i}]", takeaway.text)
        for i, point in enumerate(review_points, 1):
            errors += _style_errors(f"review_points[{i}]", point)
    if errors:
        raise VerificationError(errors)
    return SummaryResponse(
        takeaways=[
            Takeaway(text=t.text.strip(), refs=list(dict.fromkeys(t.refs)))
            for t in takeaways[:high]
        ],
        review_points=review_points[:MAX_REVIEW_POINTS],
    )


def _coach_failed() -> ApiError:
    return ApiError(
        502, ErrorCode.ANALYSIS_FAILED, "코치 응답을 만들지 못했어요. 다시 시도해 주세요."
    )


async def run_scenario(
    req: ScenarioRequest, checked: CheckedCode, stats: RunStats | None = None
) -> ScenarioResponse:
    try:
        return await runtime.run_with_correction(
            _agent("CoachScenario", COACH_SCENARIO_PROMPT, ScenarioDraft),
            scenario_input(req.code, checked.facts, req.preset, req.steps),
            _style_on_first_try(
                lambda output, check_style: verify_scenario(output, req.preset, check_style)
            ),
            stats=stats or RunStats(),
            max_turns=MAX_TURNS,
            retry_max_turns=MAX_TURNS,
        )
    except VerificationError as exc:
        raise _coach_failed() from exc


async def run_model_answer(
    req: ModelAnswerRequest, checked: CheckedCode, stats: RunStats | None = None
) -> ModelAnswerResponse:
    try:
        return await runtime.run_with_correction(
            _agent("CoachModelAnswer", COACH_MODEL_ANSWER_PROMPT, ModelAnswerDraft),
            model_answer_input(req.code, checked.facts, req.preset, req.question),
            _style_on_first_try(verify_model_answer),
            stats=stats or RunStats(),
            max_turns=MAX_TURNS,
            retry_max_turns=MAX_TURNS,
        )
    except VerificationError as exc:
        raise _coach_failed() from exc


async def run_feedback(
    req: FeedbackRequest, checked: CheckedCode, stats: RunStats | None = None
) -> FeedbackResponse:
    try:
        return await runtime.run_with_correction(
            _agent("CoachFeedback", COACH_FEEDBACK_PROMPT, FeedbackDraft),
            feedback_input(req.code, checked.facts, req.preset, req.question, req.answer),
            verify_feedback,
            stats=stats or RunStats(),
            max_turns=MAX_TURNS,
            retry_max_turns=MAX_TURNS,
        )
    except VerificationError as exc:
        raise _coach_failed() from exc


async def run_summary(req: SummaryRequest, stats: RunStats | None = None) -> SummaryResponse:
    refs = summary_refs(req)
    try:
        return await runtime.run_with_correction(
            _agent("CoachSummary", SUMMARY_PROMPT, SummaryDraft),
            summary_input(req.steps, req.decisions, req.applications),
            _style_on_first_try(
                lambda output, check_style: verify_summary(output, refs, check_style)
            ),
            stats=stats or RunStats(),
            max_turns=MAX_TURNS,
            retry_max_turns=MAX_TURNS,
        )
    except VerificationError as exc:
        raise _coach_failed() from exc
