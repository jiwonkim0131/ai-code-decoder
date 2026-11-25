"""원격 MCP 엔드포인트 `/mcp`. JSON-RPC를 HTTP로 직접 보내 로컬 MCP 클라이언트처럼 부른다."""

from __future__ import annotations

import base64
import json
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

import app as app_module
from server.config import get_settings
from server.models import Constraint
from server.sealing import Sealer, SealError
from tests.fakes import (
    AGENT_TOOLS,
    GATHER_URL,
    analysis_draft,
    constraint,
    constraints_draft,
    doc_evidence,
    final,
    judge_draft,
    lookup,
)

# 비밀값 커밋 차단 훅에 걸리지 않게 이어 붙여 만든다(test_secrets와 같은 방식).
FAKE_KEY = "sk-" + "proj-" + "abcdefghijklmnopqrstuvwxyz0123456789ABCD"
TOKEN = "test-token-1"
SEAL_KEY = "test-seal-key"
HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
INITIALIZE = {
    "protocolVersion": "2025-06-18",
    "capabilities": {},
    "clientInfo": {"name": "test", "version": "0"},
}
SCENARIO = {
    "question": "call_tool 하나가 3초 걸리면 run_tools 전체는 얼마나 걸릴까요?",
    "follow_up": "9번째 줄의 gather가 무엇을 기다리는지 보세요.",
    "hint": "가장 느린 호출이 전체 시간을 정하는지 떠올려 보세요.",
}


class McpClient:
    def __init__(self, http: TestClient, token: str | None = TOKEN) -> None:
        self.http = http
        self.headers = dict(HEADERS)
        if token is not None:
            self.headers["Authorization"] = f"Bearer {token}"
        self._id = 0

    def post(self, method: str, params: dict[str, Any] | None = None):
        self._id += 1
        body: dict[str, Any] = {"jsonrpc": "2.0", "id": self._id, "method": method}
        if params is not None:
            body["params"] = params
        return self.http.post("/mcp", headers=self.headers, json=body)

    def rpc(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self.post(method, params)
        assert response.status_code == 200, response.text
        return response.json()

    def initialize(self) -> dict[str, Any]:
        result = self.rpc("initialize", INITIALIZE)["result"]
        self.headers["MCP-Protocol-Version"] = result["protocolVersion"]
        return result

    def call(self, name: str, **arguments: Any) -> dict[str, Any]:
        return self.rpc("tools/call", {"name": name, "arguments": arguments})["result"]


@pytest.fixture
def open_app(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[..., TestClient]]:
    """lifespan(MCP 세션 관리자)까지 돌린 앱. 기본으로 MCP 토큰·봉인 키를 넣는다."""
    opened: list[TestClient] = []

    def _open(**env: str) -> TestClient:
        env = {"MCP_TOKENS": f"other-token, {TOKEN}", "MCP_SEAL_KEY": SEAL_KEY, **env}
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        get_settings.cache_clear()
        http = TestClient(app_module.create_app(), raise_server_exceptions=False)
        http.__enter__()
        opened.append(http)
        return http

    yield _open
    for http in opened:
        http.__exit__(None, None, None)


@pytest.fixture
def mcp(open_app) -> McpClient:
    client = McpClient(open_app())
    client.initialize()
    return client


@pytest.fixture
def gather_doc(docs):
    docs[("asyncio", "gather")] = doc_evidence("asyncio", "gather", "exact_match", GATHER_URL)
    return docs


def _analyze(mcp: McpClient, scripted_model) -> dict[str, Any]:
    scripted_model.extend([lookup("asyncio", "gather"), final(analysis_draft())])
    result = mcp.call("analyze_code", code=AGENT_TOOLS, focus_lines=[9])
    assert result["isError"] is False, result
    return result


def _constraint_key(mcp: McpClient, scripted_model) -> str:
    scripted_model.extend([final(constraints_draft(constraint()))])
    result = mcp.call("suggest_constraints", code=AGENT_TOOLS)
    assert result["isError"] is False, result
    return result["structuredContent"]["constraints"][0]["key"]


# --- 열림·인증 ----------------------------------------------------------------


@pytest.mark.parametrize(
    "env",
    [{}, {"MCP_TOKENS": TOKEN}, {"MCP_SEAL_KEY": SEAL_KEY}],
    ids=["nothing", "token-only", "seal-only"],
)
def test_mcp_is_closed_unless_tokens_and_seal_key_are_set(make_client, env):
    http = make_client(**env)

    response = http.post("/mcp", headers=HEADERS, json={"jsonrpc": "2.0", "id": 1})

    assert response.status_code == 404


@pytest.mark.parametrize(
    "authorization",
    [None, "Bearer wrong", f"Basic {TOKEN}", "Bearer ", TOKEN],
    ids=["missing", "wrong", "scheme", "empty", "no-scheme"],
)
def test_requests_without_valid_token_are_refused(open_app, authorization):
    headers = dict(HEADERS)
    if authorization is not None:
        headers["Authorization"] = authorization

    response = open_app().post(
        "/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 1, "method": "initialize"}
    )

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == "unauthorized"


def test_any_listed_token_is_accepted(open_app):
    client = McpClient(open_app(), token="other-token")

    assert client.post("initialize", INITIALIZE).status_code == 200


def test_initialize_gives_learning_instructions(open_app):
    result = McpClient(open_app()).initialize()

    assert result["serverInfo"]["name"] == "ai-code-decoder"
    assert "key" in result["instructions"]


# --- 도구 ---------------------------------------------------------------------


def test_lists_learning_tools_with_camel_case_schemas(mcp):
    tools = {tool["name"]: tool for tool in mcp.rpc("tools/list")["result"]["tools"]}

    assert set(tools) == {
        "scan_secrets",
        "analyze_code",
        "judge_answer",
        "suggest_constraints",
        "make_scenario",
        "give_feedback",
        "model_answer",
        "summarize",
    }
    analyze_out = tools["analyze_code"]["outputSchema"]["$defs"]["PublicDecisionPoint"]
    assert "stepId" in analyze_out["properties"]
    assert "codeOption" not in analyze_out["properties"]
    assert tools["scan_secrets"]["annotations"]["readOnlyHint"] is True


def test_scan_secrets_without_llm(mcp):
    code = f"key = '{FAKE_KEY}'\n"

    result = mcp.call("scan_secrets", code=code)

    assert result["isError"] is False
    assert result["structuredContent"]["findings"][0]["kind"] == "openai_api_key"


def test_analyze_hides_answers_behind_sealed_key(mcp, scripted_model, gather_doc):
    result = _analyze(mcp, scripted_model)

    body = result["structuredContent"]
    assert body["steps"][1]["evidence"][0]["badge"] == "official"
    dp = body["decisionPoints"][0]
    assert set(dp) == {"id", "stepId", "lines", "prompt", "options", "hint", "key"}
    # 클라이언트가 보는 모든 결과(텍스트·구조화)에 정답 필드와 조건 설명이 없다.
    raw = json.dumps(result, ensure_ascii=False)
    for hidden in ("codeOption", "conditions", "전체 대기 시간"):
        assert hidden not in raw
    scripted_model.assert_complete()


def test_judge_answer_opens_key_with_step_evidence(mcp, scripted_model, gather_doc):
    key = _analyze(mcp, scripted_model)["structuredContent"]["decisionPoints"][0]["key"]
    scripted_model.extend([final(judge_draft())])

    result = mcp.call("judge_answer", decision_key=key, choice="B", reason="실패해도 계속")

    assert result["isError"] is False, result
    verdict = result["structuredContent"]
    assert (verdict["hit"], verdict["total"]) == (1, 2)
    assert verdict["codeChoice"]["label"] == "asyncio.gather"
    # 키에 함께 봉인한 단계 근거(공식 문서)가 판정 근거로 이어진다.
    assert verdict["tradeoffs"]["evidence"]["url"] == GATHER_URL


def test_constraint_coach_fields_are_sealed(mcp, scripted_model):
    scripted_model.extend([final(constraints_draft(constraint()))])

    result = mcp.call("suggest_constraints", code=AGENT_TOOLS)

    item = result["structuredContent"]["constraints"][0]
    assert set(item) == {"id", "label", "situation", "lines", "symbol", "key"}
    raw = json.dumps(result, ensure_ascii=False)
    assert constraint()["assumption"] not in raw
    assert constraint()["rationale"] not in raw


def test_make_scenario_with_constraint_key(mcp, scripted_model):
    key = _constraint_key(mcp, scripted_model)
    scripted_model.extend([final(SCENARIO)])

    result = mcp.call("make_scenario", code=AGENT_TOOLS, constraint_key=key)

    assert result["isError"] is False, result
    assert result["structuredContent"]["constraintId"] == "k1"


@pytest.mark.parametrize("tamper", ["garbage", "flip"])
def test_forged_key_is_refused_before_llm(mcp, scripted_model, gather_doc, tamper):
    key = _analyze(mcp, scripted_model)["structuredContent"]["decisionPoints"][0]["key"]
    bad = (
        "not-a-key"
        if tamper == "garbage"
        else key[:-6] + ("A" if key[-6] != "A" else "B") + key[-5:]
    )

    result = mcp.call("judge_answer", decision_key=bad, choice="B")

    assert result["isError"] is True
    assert "[input_invalid]" in result["content"][0]["text"]
    scripted_model.assert_complete()  # 모델을 부르지 않았다


def test_constraint_key_cannot_be_used_as_decision_key(mcp, scripted_model):
    key = _constraint_key(mcp, scripted_model)

    result = mcp.call("judge_answer", decision_key=key, choice="B")

    assert result["isError"] is True


def test_api_errors_become_tool_errors(mcp):
    result = mcp.call("analyze_code", code="def broken(:\n")

    assert result["isError"] is True
    assert "[syntax_error]" in result["content"][0]["text"]


def test_secret_in_code_is_refused_without_echoing_it(mcp):
    code = f"key = '{FAKE_KEY}'\nprint(key)\n"

    result = mcp.call("analyze_code", code=code)

    assert result["isError"] is True
    assert "[secrets_detected]" in result["content"][0]["text"]
    assert "abcdefghijklmnop" not in result["content"][0]["text"]


# --- 봉인 ---------------------------------------------------------------------


class _Payload(BaseModel):
    value: str


def test_sealed_key_is_unreadable_and_round_trips():
    sealer = Sealer(SEAL_KEY)

    key = sealer.seal("decision", _Payload(value="정답은 B"))

    assert b"value" not in base64.urlsafe_b64decode(key)  # 평문 JSON이 토큰 안에 그대로 있지 않다
    assert sealer.open("decision", key, _Payload).value == "정답은 B"


def test_key_from_other_secret_or_kind_or_expired_is_refused():
    key = Sealer(SEAL_KEY).seal("constraint", Constraint.model_validate(constraint()))

    with pytest.raises(SealError):
        Sealer("other-secret").open("constraint", key, Constraint)
    with pytest.raises(SealError):
        Sealer(SEAL_KEY).open("decision", key, Constraint)
    with pytest.raises(SealError):
        Sealer(SEAL_KEY, ttl_seconds=-1).open("constraint", key, Constraint)


# --- 로그 ---------------------------------------------------------------------


def test_tool_calls_and_refused_requests_are_logged_without_content(open_app, caplog):
    http = open_app()
    client = McpClient(http)
    client.initialize()
    code = f"token = '{FAKE_KEY}'\n"

    with caplog.at_level("INFO", logger="server.request"):
        client.call("scan_secrets", code=code)
        client.call("analyze_code", code="def broken(:\n")
        McpClient(http, token="wrong").post("initialize", INITIALIZE)

    lines = [r.getMessage() for r in caplog.records if r.name == "server.request"]
    assert any(line.startswith("kind=mcp:scan status=ok") for line in lines)
    assert any(
        line.startswith("kind=mcp:analyze status=error") and "failure=syntax_error" in line
        for line in lines
    )
    assert any(line.startswith("kind=POST /mcp status=401") for line in lines)
    joined = "\n".join(lines)
    for secret in ("abcdefghijklmnop", "broken", TOKEN, "wrong"):
        assert secret not in joined
