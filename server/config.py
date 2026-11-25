"""환경변수에서 읽는 서버 설정.

로컬에서는 app.py가 .env를 먼저 읽고, 배포 환경에서는 Vercel 환경변수를 그대로 쓴다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

DEFAULT_MODEL = "gpt-4.1-mini"
# 요청 하나에서 에이전트 실행(수정 요청 포함)에 쓰는 최대 시간. 분석 p95 40초 목표에 여유를 둔 값
DEFAULT_AGENT_TIMEOUT_SECONDS = 60.0


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name) or default)
    except ValueError:
        return default


def _env_secrets(name: str) -> tuple[str, ...]:
    """쉼표로 구분한 비밀값 목록. _env_list와 달리 대소문자를 바꾸지 않는다."""
    raw = os.getenv(name) or ""
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _env_list(name: str, default: str) -> tuple[str, ...]:
    raw = os.getenv(name) or default
    return tuple(item.strip().lower() for item in raw.split(",") if item.strip())


@dataclass(frozen=True)
class Settings:
    openai_api_key: str | None
    openai_model: str
    allowed_langs: tuple[str, ...]
    enable_dev_routes: bool
    # 원격 MCP(/mcp): 둘 다 있어야 열린다(기본 닫힘). LLM 비용이 드는 공개 엔드포인트라서다.
    mcp_tokens: tuple[str, ...] = ()
    mcp_seal_key: str | None = None
    agent_timeout_seconds: float = DEFAULT_AGENT_TIMEOUT_SECONDS

    @property
    def has_openai_key(self) -> bool:
        return bool(self.openai_api_key)

    @property
    def mcp_enabled(self) -> bool:
        return bool(self.mcp_tokens and self.mcp_seal_key)


@lru_cache
def get_settings() -> Settings:
    return Settings(
        openai_api_key=os.getenv("OPENAI_API_KEY") or None,
        openai_model=os.getenv("OPENAI_MODEL") or DEFAULT_MODEL,
        allowed_langs=_env_list("ALLOWED_LANGS", "python"),
        enable_dev_routes=_env_bool("ENABLE_DEV_ROUTES"),
        mcp_tokens=_env_secrets("MCP_TOKENS"),
        mcp_seal_key=os.getenv("MCP_SEAL_KEY") or None,
        agent_timeout_seconds=_env_float("AGENT_TIMEOUT_SECONDS", DEFAULT_AGENT_TIMEOUT_SECONDS),
    )
