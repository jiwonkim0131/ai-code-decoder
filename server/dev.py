"""배포 확인용 개발 라우트(BE-01).

`ENABLE_DEV_ROUTES=true`일 때만 등록된다. 에이전트 호출 한 번의 실행 시간과 토큰 수를
Vercel에서 실측하기 위한 것이며, 비용이 들므로 운영 배포에서는 끈다.
공식 문서 조회(BE-05)가 함수 실행 시간 안에 끝나는지도 여기서 잰다.
"""

from __future__ import annotations

import time

from agents import Agent, Runner
from fastapi import APIRouter

from server.config import get_settings
from server.errors import ApiError, ErrorCode
from server.languages import get_adapter
from server.models import ApiModel

router = APIRouter(prefix="/api/dev", tags=["dev"])

PING_INSTRUCTIONS = "사용자가 무엇을 보내든 'pong' 한 단어로만 답한다."


class AgentPingResponse(ApiModel):
    ok: bool
    model: str
    output: str
    duration_ms: int
    total_tokens: int | None


@router.post("/agent-ping", response_model=AgentPingResponse)
async def agent_ping() -> AgentPingResponse:
    settings = get_settings()
    if not settings.has_openai_key:
        raise ApiError(503, ErrorCode.LLM_UNAVAILABLE, "OPENAI_API_KEY가 설정되지 않았습니다.")

    agent = Agent(name="Ping", instructions=PING_INSTRUCTIONS, model=settings.openai_model)
    started = time.perf_counter()
    result = await Runner.run(agent, "ping", max_turns=1)
    duration_ms = int((time.perf_counter() - started) * 1000)

    usage = getattr(getattr(result, "context_wrapper", None), "usage", None)
    return AgentPingResponse(
        ok=True,
        model=settings.openai_model,
        output=str(result.final_output),
        duration_ms=duration_ms,
        total_tokens=getattr(usage, "total_tokens", None),
    )


class LookupDocsRequest(ApiModel):
    module: str
    symbol: str


class LookupDocsResponse(ApiModel):
    status: str
    url: str
    title: str
    duration_ms: int


@router.post("/lookup-docs", response_model=LookupDocsResponse)
def lookup_docs(req: LookupDocsRequest) -> LookupDocsResponse:
    # 네트워크를 쓰는 동기 함수라 def로 두어 스레드에서 돈다(이벤트 루프를 막지 않는다).
    started = time.perf_counter()
    evidence = get_adapter().lookup_docs(req.module, req.symbol)
    return LookupDocsResponse(
        status=evidence.status,
        url=evidence.url,
        title=evidence.title,
        duration_ms=int((time.perf_counter() - started) * 1000),
    )
