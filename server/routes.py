"""`/api/*` 라우트. 요청·응답 모양(계약)을 먼저 고정하고, 구현은 BE-04~08에서 채운다.

코드를 받는 엔드포인트는 LLM을 부르기 전에 `check_code`(허용 언어·비밀값·정적 분석)를 거친다.
"""

from __future__ import annotations

from fastapi import APIRouter

from server.agents.analyst import run_analyst
from server.agents.judge import run_judge
from server.errors import ApiError, ErrorCode
from server.intake import check_code
from server.models import (
    AnalyzeRequest,
    AnalyzeResponse,
    ErrorResponse,
    FeedbackRequest,
    FeedbackResponse,
    JudgeRequest,
    ScanRequest,
    ScanResponse,
    ScenarioRequest,
    ScenarioResponse,
    Verdict,
)
from server.secrets import scan_secrets

ERROR_RESPONSES = {
    400: {"model": ErrorResponse, "description": "입력 오류"},
    429: {"model": ErrorResponse, "description": "호출 한도"},
    502: {"model": ErrorResponse, "description": "LLM 장애 또는 분석 실패"},
}

router = APIRouter(prefix="/api", responses=ERROR_RESPONSES)


def _not_implemented(feature: str) -> ApiError:
    return ApiError(501, ErrorCode.NOT_IMPLEMENTED, f"아직 구현되지 않았어요({feature}).")


@router.post("/scan", response_model=ScanResponse, tags=["analysis"])
async def scan(req: ScanRequest) -> ScanResponse:
    """비밀값 검사만 한다(LLM 호출 없음). 붙여넣기 직후와 "값 가리기" 뒤 재확인에 쓴다."""
    return ScanResponse(findings=scan_secrets(req.code))


@router.post("/analyze", response_model=AnalyzeResponse, tags=["analysis"])
async def analyze(req: AnalyzeRequest) -> AnalyzeResponse:
    """분석 에이전트를 실행해 단계·결정 지점·조건을 돌려준다."""
    checked = check_code(req.code, req.language)
    outside = [line for line in req.focus_lines if line > checked.facts.line_count]
    if outside:
        raise ApiError(
            400,
            ErrorCode.INPUT_INVALID,
            f"궁금한 줄이 코드 범위를 벗어났어요(코드 {checked.facts.line_count}줄).",
            fields=[{"field": "focusLines", "reason": "out_of_range"}],
        )
    return await run_analyst(req, checked)


@router.post("/judge", response_model=Verdict, tags=["learning"])
async def judge(req: JudgeRequest) -> Verdict:
    """사용자의 답을 변별 조건으로 판정한다. choice가 null이면 설명 모드다."""
    return await run_judge(req)


@router.post("/scenario", response_model=ScenarioResponse, tags=["learning"])
async def scenario(req: ScenarioRequest) -> ScenarioResponse:
    """제약 프리셋에 맞는 실패 주입 질문을 만든다."""
    check_code(req.code)
    raise _not_implemented("BE-08")


@router.post("/feedback", response_model=FeedbackResponse, tags=["learning"])
async def feedback(req: FeedbackRequest) -> FeedbackResponse:
    """실패 주입 질문에 대한 답을 평가한다."""
    check_code(req.code)
    raise _not_implemented("BE-08")
