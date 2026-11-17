"""코드를 받는 엔드포인트의 공통 입구: 허용 언어 → 비밀값 스캔 → 정적 분석.

analyze·scenario·feedback이 LLM을 부르기 전에 모두 이 순서를 거친다(설계 문서 에이전트 설계
1~3단계). 여기까지는 LLM 비용이 들지 않는다. 서버가 사실 목록을 기억하지 않으므로 요청마다
다시 만든다.
"""

from __future__ import annotations

from dataclasses import dataclass

from server.errors import ApiError, ErrorCode
from server.languages import DEFAULT_LANGUAGE, LanguageUnsupportedError, get_adapter
from server.languages.base import CodeAnalysisError, CodeSyntaxError, LanguageAdapter, StaticFacts
from server.secrets import scan_secrets


@dataclass(frozen=True)
class CheckedCode:
    adapter: LanguageAdapter
    facts: StaticFacts


def check_code(code: str, language: str = DEFAULT_LANGUAGE) -> CheckedCode:
    """LLM에 보내도 되는 코드인지 확인하고 사실 목록을 만든다. 안 되면 400 ApiError."""
    try:
        adapter = get_adapter(language)
    except LanguageUnsupportedError as exc:
        raise ApiError(
            400,
            ErrorCode.LANGUAGE_UNSUPPORTED,
            "지원하지 않는 언어예요. 지금은 Python 코드만 분석할 수 있어요.",
            supported=exc.supported,
        ) from None

    findings = scan_secrets(code)
    if findings:
        raise ApiError(
            400,
            ErrorCode.SECRETS_DETECTED,
            "API 키처럼 보이는 값이 있어요. 값을 가린 뒤 다시 시도해 주세요.",
            findings=[f.model_dump(by_alias=True) for f in findings],
        )

    try:
        facts = adapter.extract_facts(code)
    except CodeSyntaxError as exc:
        raise ApiError(400, ErrorCode.SYNTAX_ERROR, str(exc), line=exc.line) from None
    except CodeAnalysisError as exc:
        raise ApiError(400, ErrorCode.INPUT_INVALID, str(exc)) from None
    return CheckedCode(adapter=adapter, facts=facts)
