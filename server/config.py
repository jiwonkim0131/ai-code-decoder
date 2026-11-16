"""환경변수에서 읽는 서버 설정.

로컬에서는 app.py가 .env를 먼저 읽고, 배포 환경에서는 Vercel 환경변수를 그대로 쓴다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

DEFAULT_MODEL = "gpt-4.1-mini"


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_list(name: str, default: str) -> tuple[str, ...]:
    raw = os.getenv(name) or default
    return tuple(item.strip().lower() for item in raw.split(",") if item.strip())


@dataclass(frozen=True)
class Settings:
    openai_api_key: str | None
    openai_model: str
    allowed_langs: tuple[str, ...]
    enable_dev_routes: bool

    @property
    def has_openai_key(self) -> bool:
        return bool(self.openai_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings(
        openai_api_key=os.getenv("OPENAI_API_KEY") or None,
        openai_model=os.getenv("OPENAI_MODEL") or DEFAULT_MODEL,
        allowed_langs=_env_list("ALLOWED_LANGS", "python"),
        enable_dev_routes=_env_bool("ENABLE_DEV_ROUTES"),
    )
