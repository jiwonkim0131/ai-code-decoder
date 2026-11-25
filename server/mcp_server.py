"""원격 MCP 서버(`/mcp`).
배포한 서버를 사용자의 로컬 MCP 클라이언트(Claude Code 등)에 연결해 학습 흐름을 그대로 쓰게 한다.

- 서버는 사용자 로컬 파일을 읽지 않는다. 클라이언트(로컬 에이전트)가 파일을 읽어 `code`로 넘긴다.
- 도구는 `/api/*` 라우트 함수를 그대로 부른다. 입력 검사(check_code), 시간 상한, 로그,
  오류 매핑이 REST API와 같다.
- ApiError는 도구 오류(isError)로 바꿔 돌려준다.
- LLM 비용이 드는 공개 엔드포인트라 `Authorization: Bearer <MCP_TOKENS 중 하나>`가 있어야 한다.
- 정답(결정 지점의 codeOption,conditions, 제약의 assumption·rationale)은 도구 결과에
  평문으로 싣지 않는다.
- 클라이언트 화면에서 도구 결과를 펼치면 사용자가 그대로 볼 수 있기 때문이다. 대신 암호화한
  `key`를 주고, 판정,코치 도구는 그 키만 받는다(`server.sealing`).
- Vercel 같은 서버리스에서 돌도록 stateless + JSON 응답(SSE 없음)으로 연다.
"""

from __future__ import annotations

import hmac
import time
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field, ValidationError
from starlette.routing import BaseRoute, Route
from starlette.types import ASGIApp, Receive, Scope, Send

from server import routes
from server.errors import ApiError, ErrorCode, error_response
from server.logs import log_event
from server.models import (
    MAX_ANSWER_CHARACTERS,
    MAX_APPLICATIONS,
    MAX_CONSTRAINTS,
    MAX_DECISIONS,
    MAX_QUESTION_CHARACTERS,
    MAX_STEPS,
    AnalyzeMeta,
    AnalyzeRequest,
    ApiModel,
    AudienceLevel,
    Code,
    Constraint,
    ConstraintsRequest,
    DecisionPoint,
    Evidence,
    FeedbackRequest,
    FeedbackResponse,
    JudgeRequest,
    LineNumber,
    ModelAnswerRequest,
    ModelAnswerResponse,
    Option,
    OptionId,
    ScanRequest,
    ScanResponse,
    ScenarioRequest,
    ScenarioResponse,
    Step,
    SummaryApplication,
    SummaryDecision,
    SummaryRequest,
    SummaryResponse,
    Verdict,
)
from server.sealing import Sealer, SealError

MCP_PATH = "/mcp"
DECISION_KIND = "decision"
CONSTRAINT_KIND = "constraint"

INSTRUCTIONS = """\
AI Code Tutor: AI가 짜 준 Python 코드의 흐름과 설계 결정을 사용자가 스스로 판단하며 배우게
돕는다. 서버는 사용자 컴퓨터의 파일을 읽을 수 없다. 사용자가 가리킨 로컬 파일은 네가 직접 읽어
`code`로 넘겨라. 서버는 상태를 저장하지 않으니, 앞 도구가 준 값(steps, key 등)을 다음 도구에 넘겨라.
`key`는 정답이 암호화된 값이라 읽을 수 없다. 받은 그대로 넘기고, 만료됐다고 하면 다시 분석하라.

권장 순서:
1. scan_secrets: 코드에 API 키 같은 비밀값이 있으면 분석이 거절된다.
   찾으면 그 값을 가린 코드로 바꿔 넘겨라.
2. analyze_code: 흐름 단계(steps)와 결정 지점(decisionPoints)을 받는다. 단계를 차례로 설명하라.
3. 결정 지점마다 prompt, options, hint를 보여 주고 사용자의 선택(A~D)과 이유를 받아
   judge_answer에 넘겨라. 사용자가 모르겠다고 하면 choice 없이 불러 설명 모드로 보여 줘라.
   선택지 중 어느 것이 이 코드의 선택인지 네가 먼저 추측해 말하지 마라.
4. suggest_constraints → make_scenario: 바뀐 상황 질문을 내고 사용자의 답을 받아
   give_feedback으로 평가한다. 사용자가 모범 답을 원할 때만 model_answer를 불러라.
5. summarize: 마지막에 학습 기록 전체로 핵심 정리를 만든다.
"""

KeyField = Annotated[str, Field(min_length=1, max_length=16_000)]


# --- MCP용 공개 모델: 정답 필드를 빼고 봉인 키를 붙인다 -----------------------


class SealedDecision(ApiModel):
    """결정 지점 키 안에 든 값. 판정에 쓰는 단계 근거(evidence)도 함께 넣어 고정한다."""

    decision_point: DecisionPoint
    evidence: list[Evidence] = Field(default_factory=list)


class PublicDecisionPoint(ApiModel):
    id: str
    step_id: int
    lines: list[LineNumber]
    prompt: str
    options: list[Option]
    hint: str
    key: str = Field(description="judge_answer·summarize에 넘길 키. 정답이 암호화돼 있다")


class McpAnalyzeResponse(ApiModel):
    language: str
    steps: list[Step]
    decision_points: list[PublicDecisionPoint]
    meta: AnalyzeMeta


class PublicConstraint(ApiModel):
    id: str
    label: str
    situation: str
    lines: list[LineNumber]
    symbol: str
    key: str = Field(description="make_scenario 등에 넘길 키. 코치용 정보가 암호화돼 있다")


class McpConstraintsResponse(ApiModel):
    constraints: list[PublicConstraint] = Field(
        max_length=MAX_CONSTRAINTS, description="비어 있으면 설계 응용 단계를 건너뛴다"
    )


class McpSummaryDecision(ApiModel):
    decision_key: KeyField
    verdict: Verdict = Field(description="그 결정 지점에 대한 judge_answer 결과")


class McpSummaryApplication(ApiModel):
    constraint_key: KeyField
    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARACTERS)
    answer: str = Field(default="", max_length=MAX_ANSWER_CHARACTERS)
    feedback: FeedbackResponse | None = None
    model_answer: str | None = Field(default=None, max_length=MAX_ANSWER_CHARACTERS)


# --- 오류 ---------------------------------------------------------------------


def _tool_error(exc: ApiError | ValidationError | SealError) -> ToolError:
    if isinstance(exc, SealError):
        return ToolError(
            f"[{ErrorCode.INPUT_INVALID}] {exc} key가 올바르지 않거나 만료됐어요. "
            "받은 값을 그대로 넘기거나, 앞 단계(analyze_code 등)를 다시 불러 주세요."
        )
    if isinstance(exc, ApiError):
        detail = f" ({exc.details})" if exc.details else ""
        return ToolError(f"[{exc.code}] {exc.message}{detail}")
    # 입력값(코드 원문일 수 있음)은 돌려보내지 않고 위치와 사유만 준다(REST API와 같은 규칙).
    fields = ", ".join(
        f"{'.'.join(str(p) for p in err.get('loc', ()))}: {err.get('type')}" for err in exc.errors()
    )
    return ToolError(f"[{ErrorCode.INPUT_INVALID}] 입력을 확인해 주세요. {fields}")


async def _call[T](handler: Callable[[Any], Awaitable[T]], build: Callable[[], Any]) -> T:
    """라우트 함수를 부르고 도구 호출마다 로그 한 줄을 남긴다(`kind=mcp:<라우트>`).

    LLM을 부르는 도구는 agent_call이 `agent:*` 줄을 따로 남긴다. scan처럼 LLM이 없는 도구도
    호출량을 볼 수 있게 여기서 남긴다. 로그 규칙대로 입력·출력 내용은 쓰지 않는다."""
    started = time.perf_counter()
    failure: str | None = None
    try:
        return await handler(build())
    except ApiError as exc:
        failure = exc.code
        raise _tool_error(exc) from None
    except (ValidationError, SealError) as exc:
        failure = ErrorCode.INPUT_INVALID
        raise _tool_error(exc) from None
    except BaseException:
        failure = ErrorCode.INTERNAL
        raise
    finally:
        log_event(
            kind=f"mcp:{handler.__name__}",
            status="error" if failure else "ok",
            duration_ms=int((time.perf_counter() - started) * 1000),
            failure=failure,
        )


# --- 서버 ---------------------------------------------------------------------


def create_mcp_server(sealer: Sealer) -> MCPServer:
    """앱마다 새로 만든다(세션 관리자는 한 번만 run할 수 있다)."""
    server = MCPServer(
        name="ai-code-tutor",
        title="AI Code Tutor",
        instructions=INSTRUCTIONS,
        version="0.1.0",
    )
    read_only = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
    llm = ToolAnnotations(readOnlyHint=True, openWorldHint=True)

    def open_decision(key: str) -> SealedDecision:
        return sealer.open(DECISION_KIND, key, SealedDecision)

    def open_constraint(key: str) -> Constraint:
        return sealer.open(CONSTRAINT_KIND, key, Constraint)

    @server.tool(annotations=read_only)
    async def scan_secrets(code: Code) -> ScanResponse:
        """코드에서 API 키·토큰 같은 비밀값 후보를 찾는다(LLM 호출 없음). 분석 전에 먼저 부른다."""
        return await _call(routes.scan, lambda: ScanRequest(code=code))

    @server.tool(annotations=llm)
    async def analyze_code(
        code: Annotated[Code, Field(description="Python 코드 원문(로컬 파일을 읽어 넘긴다)")],
        level: Annotated[
            AudienceLevel, Field(description="basic=입문, work=실무, deep=심화")
        ] = "work",
        focus_lines: Annotated[
            list[LineNumber], Field(description="특히 궁금한 줄 번호(1부터)")
        ] = [],  # noqa: B006 - 스키마 기본값. 함수 안에서 바꾸지 않는다
        question: Annotated[
            str, Field(max_length=MAX_QUESTION_CHARACTERS, description="사용자의 질문")
        ] = "",
    ) -> McpAnalyzeResponse:
        """코드 흐름을 단계(steps)로 나누고 설계 결정 지점(decisionPoints)을 찾는다."""
        result = await _call(
            routes.analyze,
            lambda: AnalyzeRequest(
                code=code, level=level, focus_lines=focus_lines, question=question
            ),
        )
        evidence_by_step = {s.id: s.evidence for s in result.steps}
        return McpAnalyzeResponse(
            language=result.language,
            steps=result.steps,
            meta=result.meta,
            decision_points=[
                PublicDecisionPoint(
                    id=dp.id,
                    step_id=dp.step_id,
                    lines=dp.lines,
                    prompt=dp.prompt,
                    options=dp.options,
                    hint=dp.hint,
                    key=sealer.seal(
                        DECISION_KIND,
                        SealedDecision(
                            decision_point=dp, evidence=evidence_by_step.get(dp.step_id, [])
                        ),
                    ),
                )
                for dp in result.decision_points
            ],
        )

    @server.tool(annotations=llm)
    async def judge_answer(
        decision_key: Annotated[KeyField, Field(description="analyze_code 결정 지점의 key")],
        choice: Annotated[
            OptionId | None, Field(description="사용자의 선택. 모르겠다고 하면 null(설명 모드)")
        ] = None,
        reason: Annotated[
            str, Field(max_length=MAX_ANSWER_CHARACTERS, description="사용자가 말한 이유")
        ] = "",
        level: AudienceLevel = "work",
    ) -> Verdict:
        """사용자의 선택과 이유를 결정 조건으로 판정한다.

        이 코드의 실제 선택과 선택지별 트레이드오프도 함께 준다."""

        def build() -> JudgeRequest:
            sealed = open_decision(decision_key)
            return JudgeRequest(
                decision_point=sealed.decision_point,
                evidence=sealed.evidence,
                choice=choice,
                reason=reason,
                level=level,
            )

        return await _call(routes.judge, build)

    @server.tool(annotations=llm)
    async def suggest_constraints(
        code: Code,
        steps: Annotated[
            list[Step], Field(max_length=MAX_STEPS, description="analyze_code가 준 steps")
        ] = [],  # noqa: B006
    ) -> McpConstraintsResponse:
        """설계 응용에서 바꿔 볼 운영 제약(트래픽, 실패, 지연 등)을 코드에 맞춰 고른다.

        비어 있으면 이 코드에는 설계 응용 단계를 건너뛴다."""
        result = await _call(routes.constraints, lambda: ConstraintsRequest(code=code, steps=steps))
        return McpConstraintsResponse(
            constraints=[
                PublicConstraint(
                    id=c.id,
                    label=c.label,
                    situation=c.situation,
                    lines=c.lines,
                    symbol=c.symbol,
                    key=sealer.seal(CONSTRAINT_KIND, c),
                )
                for c in result.constraints
            ]
        )

    @server.tool(annotations=llm)
    async def make_scenario(
        code: Code,
        constraint_key: Annotated[KeyField, Field(description="suggest_constraints 제약의 key")],
        steps: Annotated[list[Step], Field(max_length=MAX_STEPS)] = [],  # noqa: B006
    ) -> ScenarioResponse:
        """고른 제약에서 "이 코드는 어떻게 바뀌어야 할까?" 질문을 만든다."""
        return await _call(
            routes.scenario,
            lambda: ScenarioRequest(
                code=code, constraint=open_constraint(constraint_key), steps=steps
            ),
        )

    @server.tool(annotations=llm)
    async def give_feedback(
        code: Code,
        constraint_key: KeyField,
        question: Annotated[
            str, Field(min_length=1, max_length=MAX_QUESTION_CHARACTERS, description="질문")
        ],
        answer: Annotated[
            str, Field(min_length=1, max_length=MAX_ANSWER_CHARACTERS, description="사용자의 답")
        ],
    ) -> FeedbackResponse:
        """make_scenario 질문에 대한 사용자의 답을 평가한다."""
        return await _call(
            routes.feedback,
            lambda: FeedbackRequest(
                code=code,
                constraint=open_constraint(constraint_key),
                question=question,
                answer=answer,
            ),
        )

    @server.tool(annotations=llm)
    async def model_answer(
        code: Code,
        constraint_key: KeyField,
        question: Annotated[str, Field(min_length=1, max_length=MAX_QUESTION_CHARACTERS)],
    ) -> ModelAnswerResponse:
        """make_scenario 질문의 모범 답을 만든다. 사용자가 원할 때만 부른다."""
        return await _call(
            routes.model_answer,
            lambda: ModelAnswerRequest(
                code=code, constraint=open_constraint(constraint_key), question=question
            ),
        )

    @server.tool(annotations=llm)
    async def summarize(
        steps: Annotated[list[Step], Field(min_length=1, max_length=MAX_STEPS)],
        decisions: Annotated[
            list[McpSummaryDecision],
            Field(max_length=MAX_DECISIONS, description="결정 지점 key와 judge_answer 결과 쌍"),
        ] = [],  # noqa: B006
        applications: Annotated[
            list[McpSummaryApplication],
            Field(max_length=MAX_APPLICATIONS, description="설계 응용 질문·답·피드백"),
        ] = [],  # noqa: B006
    ) -> SummaryResponse:
        """학습 기록 전체로 핵심 정리와 다시 볼 점을 만든다. 코드는 넘기지 않는다."""

        def build() -> SummaryRequest:
            return SummaryRequest(
                steps=steps,
                decisions=[
                    SummaryDecision(
                        decision_point=open_decision(d.decision_key).decision_point,
                        verdict=d.verdict,
                    )
                    for d in decisions
                ],
                applications=[
                    SummaryApplication(
                        constraint=open_constraint(a.constraint_key),
                        question=a.question,
                        answer=a.answer,
                        feedback=a.feedback,
                        model_answer=a.model_answer,
                    )
                    for a in applications
                ],
            )

        return await _call(routes.summary, build)

    return server


# --- HTTP 연결 ----------------------------------------------------------------


class BearerAuth:
    """`Authorization: Bearer <토큰>`이 MCP_TOKENS 중 하나와 같을 때만 통과시킨다.

    토큰은 비교에만 쓰고 로그·응답에 남기지 않는다."""

    def __init__(self, app: ASGIApp, tokens: tuple[str, ...]) -> None:
        self.app = app
        self.tokens = tuple(t.encode() for t in tokens)

    def _allowed(self, scope: Scope) -> bool:
        header = dict(scope.get("headers") or []).get(b"authorization", b"")
        scheme, _, token = header.partition(b" ")
        if scheme.lower() != b"bearer" or not token.strip():
            return False
        token = token.strip()
        # 모든 토큰과 비교해 어느 토큰과 맞았는지가 응답 시간에 드러나지 않게 한다.
        return any([hmac.compare_digest(token, allowed) for allowed in self.tokens])

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and not self._allowed(scope):
            response = error_response(401, ErrorCode.UNAUTHORIZED, "MCP 토큰이 필요해요.")
            response.headers["WWW-Authenticate"] = "Bearer"
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


def mcp_routes(server: MCPServer, tokens: tuple[str, ...]) -> list[BaseRoute]:
    """FastAPI 앱에 붙일 `/mcp` 라우트. 마운트하면 `/mcp/`로 리다이렉트되므로 라우트를 직접 붙인다.

    Host 헤더 검사(DNS 리바인딩 방지)는 끈다. 그 검사는 로컬에서만 도는 서버가 브라우저의 다른
    사이트로부터 호출되는 것을 막기 위한 것이고, 이 서버는 토큰으로 막는 공개 엔드포인트다.
    """
    http_app = server.streamable_http_app(
        streamable_http_path=MCP_PATH,
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    built = list(http_app.routes)
    for route in built:
        if isinstance(route, Route):
            route.app = BearerAuth(route.app, tokens)
    return built
