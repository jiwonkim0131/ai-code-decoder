"""테스트 공통 설정. 로컬 .env 값과 무관하게, OpenAI를 실제로 부르지 않고 돈다."""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient

import app as app_module
from server.config import get_settings

ENV_KEYS = ("OPENAI_API_KEY", "OPENAI_MODEL", "ALLOWED_LANGS", "ENABLE_DEV_ROUTES")


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def make_client(monkeypatch: pytest.MonkeyPatch) -> Callable[..., TestClient]:
    """환경변수를 지정해 새 앱을 만든다. 예: make_client(ENABLE_DEV_ROUTES="true")."""

    def _make(**env: str) -> TestClient:
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        get_settings.cache_clear()
        return TestClient(app_module.create_app(), raise_server_exceptions=False)

    return _make


@pytest.fixture
def client(make_client: Callable[..., TestClient]) -> TestClient:
    return make_client()
