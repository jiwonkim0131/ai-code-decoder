"""테스트 공통 설정. 로컬 .env 값과 무관하게, OpenAI를 실제로 부르지 않고 돈다."""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from agents.testing import ScriptedModel
from fastapi.testclient import TestClient

import app as app_module
from server.agents import runtime
from server.config import get_settings
from server.docs.documentation import DocumentationEvidence
from server.languages.python import PythonAdapter
from tests.fakes import doc_evidence

ENV_KEYS = (
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
    "ALLOWED_LANGS",
    "ENABLE_DEV_ROUTES",
    "AGENT_TIMEOUT_SECONDS",
)


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


@pytest.fixture(autouse=True)
def scripted_model(monkeypatch: pytest.MonkeyPatch) -> ScriptedModel:
    """에이전트 모델을 대본 모델로 바꾼다. 대본이 비어 있으면 모델 호출 자체가 실패한다.

    테스트는 `scripted_model.extend([...])`로 모델 응답(도구 호출, 최종 출력)을 차례로 넣는다.
    """
    model = ScriptedModel()
    monkeypatch.setattr(runtime, "agent_model", lambda settings: model)
    return model


@pytest.fixture
def docs(monkeypatch: pytest.MonkeyPatch) -> dict[tuple[str, str], DocumentationEvidence]:
    """문서 조회를 네트워크 없이 돌린다. 등록하지 않은 (module, symbol)은 page_only."""
    table: dict[tuple[str, str], DocumentationEvidence] = {}

    def lookup(self, module: str, symbol: str) -> DocumentationEvidence:
        return table.get((module, symbol)) or doc_evidence(module, symbol, "page_only")

    monkeypatch.setattr(PythonAdapter, "lookup_docs", lookup)
    return table
