"""코치 `POST /api/scenario`, `POST /api/feedback`(BE-08).

도구 없는 구조화 호출(Coach)이다. 코드를 받으므로 라우트에서 analyze와 같은 입구 검사(허용 언어,
비밀값, 정적 분석)를 거치고, 서버가 사실 목록을 기억하지 않으니 요청마다 다시 만든다.
학습 정리(짚은 조건 수 등)는 FE가 판정 결과로 계산한다.
"""

from __future__ import annotations

from agents import Agent, MaxTurnsExceeded, ModelBehaviorError

from server.agents import runtime
from server.agents.prompts import (
    COACH_FEEDBACK_PROMPT,
    COACH_SCENARIO_PROMPT,
    feedback_input,
    scenario_input,
)
from server.agents.runtime import RunStats
from server.agents.schemas import FeedbackDraft, ScenarioDraft
from server.config import get_settings
from server.errors import ApiError, ErrorCode
from server.intake import CheckedCode
from server.models import FeedbackRequest, FeedbackResponse, ScenarioRequest, ScenarioResponse
from server.verify import VerificationError

MAX_TURNS = 1
MAX_POINTS = 3


def _agent(name: str, instructions: str, output_type: type) -> Agent[None]:
    return Agent(
        name=name,
        instructions=instructions,
        model=runtime.agent_model(get_settings()),
        output_type=output_type,
    )


def verify_scenario(draft: ScenarioDraft, preset: str) -> ScenarioResponse:
    question = draft.question.strip()
    errors: list[str] = []
    if question.count("?") != 1 or not question.endswith("?"):
        errors.append(
            f"question은 물음표가 문장 끝에 하나만 있는 질문 하나여야 합니다"
            f"(현재 물음표 {question.count('?')}개)."
        )
    if not draft.follow_up.strip():
        errors.append("follow_up이 비어 있습니다.")
    if errors:
        raise VerificationError(errors)
    return ScenarioResponse(preset=preset, question=question, follow_up=draft.follow_up.strip())


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
            lambda output: verify_scenario(output, req.preset),
            stats=stats or RunStats(),
            max_turns=MAX_TURNS,
            retry_max_turns=MAX_TURNS,
        )
    except (VerificationError, MaxTurnsExceeded, ModelBehaviorError) as exc:
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
    except (VerificationError, MaxTurnsExceeded, ModelBehaviorError) as exc:
        raise _coach_failed() from exc
