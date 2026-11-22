"""언어 어댑터 인터페이스와 정적 분석 사실 목록(BE-03).

언어마다 다른 부분(정적 분석, 공식 문서 조회, 프롬프트 힌트)은 어댑터 하나에 모은다.
에이전트와 라우트는 어댑터의 메서드만 부르고 `ast` 같은 파서를 직접 쓰지 않는다.
StaticFacts는 서버 내부 값이라 API 응답에는 나가지 않고, 에이전트 입력과 신뢰 컨텍스트에만 쓴다.
"""

from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from server.docs.documentation import DocumentationEvidence
from server.models import AudienceLevel


class CodeAnalysisError(ValueError):
    """코드를 분석할 수 없을 때. 메시지는 사용자에게 그대로 보여도 되는 문구다."""


class CodeSyntaxError(CodeAnalysisError):
    """파서가 코드를 읽지 못했다. line은 1부터이며 알 수 없으면 None이다."""

    def __init__(self, message: str, line: int | None) -> None:
        super().__init__(message)
        self.line = line


class Capabilities(BaseModel):
    """어댑터가 할 수 있는 검증. false인 항목은 검증을 낮춰서 동작한다."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    static_verification: bool
    official_docs: bool


class _Fact(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ImportFact(_Fact):
    module: str
    name: str | None = Field(description="from-import로 가져온 이름. `import x`면 None")
    local_name: str = Field(description="코드 안에서 쓰는 이름(as 별칭 포함)")
    line: int


class SymbolFact(_Fact):
    """코드 안에서 정의한 함수·클래스. name은 `Class.method`처럼 바깥 정의를 포함한다."""

    name: str
    kind: Literal["function", "async_function", "class"]
    line: int
    end_line: int


class CallFact(_Fact):
    """호출 하나. 같은 (name, module, symbol)은 하나로 묶고 나온 줄을 모두 lines에 담는다."""

    name: str = Field(description="코드에 적힌 호출 이름(예: asyncio.gather)")
    kind: Literal["function", "method"]
    module: str = Field(description="해석한 모듈. 모르면 빈 문자열")
    symbol: str = Field(description="문서 조회용 심볼 이름")
    resolved: bool = Field(description="정적 분석으로 모듈과 심볼을 모두 해석했는지")
    expression: str = Field(description="가장 앞 줄에 나온 호출식 원문")
    lines: list[int] = Field(description="이 호출이 시작하는 줄 번호들(오름차순)")
    end_line: int = Field(description="가장 앞 줄 호출식의 마지막 줄")
    used_as_input_of: str | None = Field(
        default=None, description="이 호출 결과를 바로 받는 바깥 호출 이름"
    )


class StaticFacts(_Fact):
    """어댑터가 코드에서 뽑은 사실 목록. 코드를 실행하지 않고 만든다."""

    language: str
    line_count: int
    imports: list[ImportFact]
    symbols: list[SymbolFact]
    calls: list[CallFact]
    syntax: list[str] = Field(description="눈여겨볼 문법 형태(async/await, f-string 등)")

    def has_symbol(self, module: str, symbol: str) -> bool:
        """문서 조회 인자가 사실 목록 안에 있는지. 모델이 지어낸 심볼은 밖으로 나가지 않는다."""
        return any(
            call.resolved and call.module == module and call.symbol == symbol for call in self.calls
        )


class LanguageAdapter(Protocol):
    id: str
    capabilities: Capabilities

    def extract_facts(self, code: str) -> StaticFacts:
        """코드를 실행하지 않고 사실 목록을 만든다. 읽을 수 없으면 CodeAnalysisError."""
        ...

    def lookup_docs(self, module: str, symbol: str) -> DocumentationEvidence:
        """공식 문서를 조회한다. 네트워크를 쓰므로 이벤트 루프 밖(스레드)에서 부른다."""
        ...

    def prompt_hints(self, level: AudienceLevel) -> str:
        """언어 관용구와 설명 수준별 문체를 프롬프트에 덧붙일 문장으로 돌려준다."""
        ...

    def operational_lines(self, facts: StaticFacts) -> set[int] | None:
        """외부 호출·동시성·대기처럼 운영 조건에 따라 동작이 달라지는 호출이 있는 줄.

        설계 응용의 제약은 이 줄에만 걸 수 있고, 비어 있으면 제약을 만들지 않는다(단계 건너뜀).
        정적 분석으로 가를 수 없는 언어는 None을 돌려주고, 그때는 모델이 고른 줄을 쓴다."""
        ...
