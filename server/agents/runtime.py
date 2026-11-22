"""Agents SDK 전역 설정과 공통 실행 틀.

코드 원문이 서버 밖(OpenAI 트레이싱 대시보드)에 남지 않도록 트레이싱을 끈다.
분석·판정·코치는 모두 run_with_correction(실행 → 코드 검증 → 1회 수정 요청)으로 돈다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from agents import Agent, Model, Runner, set_default_openai_key, set_tracing_disabled

from server.config import Settings
from server.verify import VerificationError

_configured = False


def configure_agents(settings: Settings) -> None:
    """앱 시작 시 한 번 호출한다. 키가 없으면 키 설정만 건너뛴다."""
    global _configured
    if _configured:
        return
    set_tracing_disabled(True)
    if settings.openai_api_key:
        set_default_openai_key(settings.openai_api_key, use_for_tracing=False)
    _configured = True


def agent_model(settings: Settings) -> str | Model:
    """에이전트가 쓸 모델. 테스트는 이 함수를 바꿔 OpenAI 대신 ScriptedModel을 넣는다."""
    return settings.openai_model


@dataclass
class RunStats:
    """한 요청에서 에이전트 실행에 쓴 토큰과 실행 횟수(로그용)."""

    tokens: int = 0
    runs: int = 0
    tool_calls: int = 0

    def add(self, result: Any) -> None:
        usage = getattr(getattr(result, "context_wrapper", None), "usage", None)
        self.tokens += getattr(usage, "total_tokens", 0) or 0
        self.runs += 1


def correction_message(exc: VerificationError) -> dict[str, str]:
    return {
        "role": "user",
        "content": f"출력에 다음 오류가 있다. 고쳐서 다시 출력하라.\n{exc.report()}",
    }


async def run_with_correction[T](
    agent: Agent[Any],
    user_input: str,
    verify: Callable[[Any], T],
    *,
    stats: RunStats,
    max_turns: int,
    retry_max_turns: int = 2,
    context: Any = None,
    may_retry: Callable[[], bool] = lambda: True,
) -> T:
    """실행하고 코드로 검증한다. 실패하면 처음부터 다시 돌리지 않고 같은 대화에 검증 오류를
    붙여 모델이 1회 고치게 한다. 그래도 실패하면 VerificationError가 그대로 올라간다."""
    result = await Runner.run(agent, user_input, context=context, max_turns=max_turns)
    stats.add(result)
    try:
        return verify(result.final_output)
    except VerificationError as exc:
        if not may_retry():
            raise
        retry_input = [*result.to_input_list(), correction_message(exc)]
        result = await Runner.run(agent, retry_input, context=context, max_turns=retry_max_turns)
        stats.add(result)
        return verify(result.final_output)
