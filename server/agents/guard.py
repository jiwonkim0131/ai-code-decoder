"""에이전트 호출 보호와 오류 매핑(BE-09).

LLM을 부르는 라우트는 모두 `agent_call`로 감싼다.
- 시간 상한(AGENT_TIMEOUT_SECONDS) 안에 끝나지 않으면 502 llm_unavailable
- OpenAI·Agents SDK 예외를 오류 코드로 바꾼다(llm_unavailable, rate_limited, analysis_failed)
- 요청마다 로그 한 줄: 종류, 소요 시간, 입력 줄 수, 토큰 수, 도구 호출 수, 실패 코드(코드 원문 없음)
사용자별 호출 한도는 저장소가 필요해 만들지 않는다(OpenAI 월 한도와 입력·턴·토큰 상한이 방어선).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import openai
from agents import AgentsException, MaxTurnsExceeded, ModelBehaviorError, ModelTimeoutError

from server.agents import runtime
from server.agents.runtime import RunStats
from server.config import get_settings
from server.errors import ApiError, ErrorCode
from server.logs import log_event

logger = logging.getLogger("server.agents")

RETRY_MESSAGE = "잠시 후 다시 시도해 주세요."


def _llm_unavailable(message: str = "AI 응답을 받지 못했어요. 다시 시도해 주세요.") -> ApiError:
    return ApiError(502, ErrorCode.LLM_UNAVAILABLE, message)


def map_agent_error(exc: Exception) -> ApiError:
    """에이전트 실행 중 난 예외를 API 오류로. 메시지에 코드·응답 내용을 담지 않는다."""
    if isinstance(exc, TimeoutError | ModelTimeoutError | openai.APITimeoutError):
        return _llm_unavailable("응답이 너무 오래 걸려요. 코드를 줄이거나 다시 시도해 주세요.")
    if isinstance(exc, openai.RateLimitError):
        # 월 사용 한도 소진(insufficient_quota)은 기다려도 풀리지 않는다.
        if getattr(exc, "code", None) == "insufficient_quota":
            return _llm_unavailable("지금은 AI 사용 한도에 도달했어요. 나중에 다시 시도해 주세요.")
        return ApiError(429, ErrorCode.RATE_LIMITED, f"요청이 많아요. {RETRY_MESSAGE}")
    if isinstance(exc, openai.OpenAIError):
        return _llm_unavailable()
    if isinstance(exc, MaxTurnsExceeded | ModelBehaviorError | AgentsException):
        return ApiError(502, ErrorCode.ANALYSIS_FAILED, f"분석에 실패했어요. {RETRY_MESSAGE}")
    return _llm_unavailable()


def _require_llm() -> None:
    settings = get_settings()
    if isinstance(runtime.agent_model(settings), str) and not settings.has_openai_key:
        raise ApiError(503, ErrorCode.LLM_UNAVAILABLE, "AI 서비스가 설정되지 않았어요.")


@asynccontextmanager
async def agent_call(kind: str, lines: int | None = None) -> AsyncIterator[RunStats]:
    """에이전트 실행 하나를 감싼다. 넘겨준 RunStats에 토큰·도구 호출 수가 쌓인다."""
    _require_llm()
    stats = RunStats()
    started = time.perf_counter()
    failure: str | None = None
    try:
        async with asyncio.timeout(get_settings().agent_timeout_seconds):
            yield stats
    except ApiError as exc:
        failure = exc.code
        raise
    except Exception as exc:
        error = map_agent_error(exc)
        failure = error.code
        logger.warning("agent_error kind=%s type=%s", kind, type(exc).__name__)
        raise error from exc
    finally:
        log_event(
            kind=f"agent:{kind}",
            duration_ms=int((time.perf_counter() - started) * 1000),
            lines=lines,
            tokens=stats.tokens,
            tool_calls=stats.tool_calls,
            failure=failure,
        )
