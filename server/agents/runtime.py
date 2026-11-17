"""Agents SDK 전역 설정.

코드 원문이 서버 밖(OpenAI 트레이싱 대시보드)에 남지 않도록 트레이싱을 끈다.
"""

from __future__ import annotations

from agents import Model, set_default_openai_key, set_tracing_disabled

from server.config import Settings

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
