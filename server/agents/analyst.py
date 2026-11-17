"""분석 에이전트 `POST /api/analyze`(BE-06).

도구는 lookup_docs 하나다. 코드와 사실 목록은 입력에 넣고, 같은 값을 신뢰 컨텍스트에도 두어
도구 인자 검증에 쓴다. 출력은 verify.py가 검증하고, 실패하면 같은 대화에 오류 목록을 붙여
1회 고치게 한다(요청 시작 후 RETRY_BUDGET_SECONDS 안일 때만).
도구 정의 방식은 스터디 레포 `agent/tools.py`, 실행 설정은 `agent/decoder.py`를 따랐다.
"""

from __future__ import annotations

from agents import (
    Agent,
    ModelSettings,
    RunContextWrapper,
    UserError,
    function_tool,
)

from server.agents import runtime
from server.agents.context import AnalystContext, ToolContractError
from server.agents.prompts import ANALYST_PROMPT, analyst_input
from server.agents.runtime import RunStats
from server.agents.schemas import AnalysisDraft
from server.config import get_settings
from server.errors import ApiError, ErrorCode
from server.intake import CheckedCode
from server.models import AnalyzeRequest, AnalyzeResponse
from server.verify import VerificationError, verify_analysis

MAX_TURNS = 6  # lookup_docs 최대 5 + 최종 출력 1 (parallel_tool_calls=False)
RETRY_MAX_TURNS = 3
RETRY_BUDGET_SECONDS = 20.0
MAX_OUTPUT_TOKENS = 6000  # 단계·결정 지점 JSON이 잘리지 않을 만큼(BE-09 출력 토큰 상한)

UNSUPPORTED_LOOKUP = (
    '{"status": "unsupported", "excerpt": "facts.calls에 있는 (module, symbol)만 조회할 수 있다."}'
)


@function_tool(strict_mode=True, failure_error_function=None)
def lookup_docs(ctx: RunContextWrapper[AnalystContext], module: str, symbol: str) -> str:
    """공식 문서를 조회한다. facts.calls에 있는 (module, symbol)만 조회할 수 있고 최대 5회다.

    Args:
        module: 호출의 module (예: asyncio)
        symbol: 호출의 symbol (예: gather)
    """
    run = ctx.context
    if not run.record_lookup(module, symbol):  # 5회 초과면 ToolContractError
        return UNSUPPORTED_LOOKUP  # 모델이 지어낸 심볼은 앱 밖으로 나가지 않는다
    evidence = run.adapter.lookup_docs(module, symbol)
    run.docs.append(evidence)
    return evidence.model_dump_json(include={"status", "module", "symbol", "title", "excerpt"})


def build_analyst(checked: CheckedCode, level: str) -> Agent[AnalystContext]:
    settings = get_settings()
    return Agent[AnalystContext](
        name="Analyst",
        instructions=f"{ANALYST_PROMPT}\n{checked.adapter.prompt_hints(level)}",
        model=runtime.agent_model(settings),
        tools=[lookup_docs],
        model_settings=ModelSettings(parallel_tool_calls=False, max_tokens=MAX_OUTPUT_TOKENS),
        output_type=AnalysisDraft,
    )


async def run_analyst(
    req: AnalyzeRequest, checked: CheckedCode, stats: RunStats | None = None
) -> AnalyzeResponse:
    model_name = get_settings().openai_model
    ctx = AnalystContext(
        code=req.code, level=req.level, adapter=checked.adapter, facts=checked.facts
    )
    stats = stats or RunStats()
    try:
        return await runtime.run_with_correction(
            build_analyst(checked, req.level),
            analyst_input(req.code, checked.facts, req.question, req.focus_lines, req.level),
            lambda output: verify_analysis(output, ctx, model_name),
            stats=stats,
            context=ctx,
            max_turns=MAX_TURNS,
            retry_max_turns=RETRY_MAX_TURNS,
            may_retry=lambda: ctx.elapsed() <= RETRY_BUDGET_SECONDS,
        )
    except UserError as exc:
        # 도구 안의 예외는 SDK가 UserError로 감싼다(failure_error_function=None).
        if not isinstance(exc.__cause__, ToolContractError):
            raise
        raise _analysis_failed() from exc
    except (VerificationError, ToolContractError) as exc:
        raise _analysis_failed() from exc
    finally:
        stats.tool_calls = len(ctx.calls)


def _analysis_failed() -> ApiError:
    return ApiError(502, ErrorCode.ANALYSIS_FAILED, "분석에 실패했어요. 다시 시도해 주세요.")
