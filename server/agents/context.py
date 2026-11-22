"""분석 에이전트의 신뢰 컨텍스트와 도구 계약(BE-06).

스터디 레포 `agent/run_context.py`의 패턴(RunContextWrapper로 넘기는 신뢰 입력, 호출 기록,
ToolContractError)만 가져왔다. 코드·수준·사실 목록은 여기에만 있고 모델은 도구 인자로 바꿀 수 없다.
레포의 "정확히 1회" 검사 대신 lookup_docs의 상한(수정 요청 실행까지 합산)과 인자 검사를 둔다.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from server.docs.documentation import DocumentationEvidence
from server.languages.base import LanguageAdapter, StaticFacts
from server.models import AudienceLevel, ToolCall

MAX_DOC_LOOKUPS = 5


class ToolContractError(RuntimeError):
    """도구 계약 위반(호출 횟수 초과 등). 분석 실패(502 analysis_failed)로 끝난다."""


@dataclass
class ToolCallRecord:
    name: str
    module: str
    symbol: str
    accepted: bool  # 사실 목록 안의 인자라서 실제로 조회했는지


@dataclass
class AnalystContext:
    code: str
    level: AudienceLevel
    adapter: LanguageAdapter
    facts: StaticFacts
    started: float = field(default_factory=time.perf_counter)
    calls: list[ToolCallRecord] = field(default_factory=list)
    docs: list[DocumentationEvidence] = field(default_factory=list)
    tokens: int = 0

    def elapsed(self) -> float:
        return time.perf_counter() - self.started

    def record_lookup(self, module: str, symbol: str) -> bool:
        """호출을 기록하고, 사실 목록 안의 인자인지 돌려준다. 상한을 넘으면 ToolContractError."""
        lookups = sum(1 for call in self.calls if call.name == "lookup_docs")
        if lookups >= MAX_DOC_LOOKUPS:
            raise ToolContractError(f"lookup_docs는 최대 {MAX_DOC_LOOKUPS}회까지 부를 수 있다.")
        accepted = self.facts.has_symbol(module, symbol)
        self.calls.append(ToolCallRecord("lookup_docs", module, symbol, accepted))
        return accepted

    def tool_calls(self) -> list[ToolCall]:
        """응답 meta에 싣는 도구 호출 목록. 인자는 모듈·심볼 이름뿐이다."""
        return [
            ToolCall(name=call.name, args={"module": call.module, "symbol": call.symbol})
            for call in self.calls
        ]
