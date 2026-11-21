"""비용·남용 보호와 오류 매핑(BE-09)."""

from __future__ import annotations

import asyncio
import logging

import httpx
import openai
import pytest
from agents import MaxTurnsExceeded
from agents.testing import ModelStep

from server.agents import analyst, coach, judge, runtime
from server.limits import MAX_BODY_BYTES
from tests.fakes import AGENT_TOOLS, final

SCENARIO = {
    "question": "어떻게 될까요?",
    "follow_up": "9번째 줄을 보세요.",
    "hint": "힌트",
}
SCENARIO_BODY = {"code": AGENT_TOOLS, "preset": "latency"}


def _status_error(cls, status: int, code: str | None = None):
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    response = httpx.Response(status, request=request)
    return cls("error", response=response, body={"code": code} if code else None)


@pytest.mark.parametrize(
    "error,status,code",
    [
        (_status_error(openai.RateLimitError, 429), 429, "rate_limited"),
        (_status_error(openai.RateLimitError, 429, "insufficient_quota"), 502, "llm_unavailable"),
        (_status_error(openai.InternalServerError, 500), 502, "llm_unavailable"),
        (_status_error(openai.AuthenticationError, 401), 502, "llm_unavailable"),
        (
            openai.APITimeoutError(httpx.Request("POST", "https://api.openai.com")),
            502,
            "llm_unavailable",
        ),
        (MaxTurnsExceeded("max turns"), 502, "analysis_failed"),
    ],
)
def test_agent_errors_are_mapped(client, scripted_model, error, status, code):
    scripted_model.extend([ModelStep.raise_error(error)])

    response = client.post("/api/scenario", json=SCENARIO_BODY)

    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert "error" not in response.json()["error"]["message"]  # 원래 예외 메시지를 내보내지 않음


def test_slow_agent_hits_time_limit(make_client, scripted_model):
    async def slow(call):
        await asyncio.sleep(1)
        return final(SCENARIO)

    scripted_model.extend([ModelStep.respond(slow)])
    client = make_client(AGENT_TIMEOUT_SECONDS="0.05")

    response = client.post("/api/scenario", json=SCENARIO_BODY)

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "llm_unavailable"


def test_missing_openai_key_is_503_before_calling(client, monkeypatch):
    monkeypatch.setattr(runtime, "agent_model", lambda settings: settings.openai_model)

    response = client.post("/api/scenario", json=SCENARIO_BODY)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "llm_unavailable"


def test_agent_log_has_tokens_and_failure_but_no_code(client, scripted_model, caplog):
    scripted_model.extend([final(SCENARIO)])
    with caplog.at_level(logging.INFO):
        client.post("/api/scenario", json=SCENARIO_BODY)
        scripted_model.extend([final({**SCENARIO, "question": "a? b?"})] * 2)
        client.post("/api/scenario", json=SCENARIO_BODY)

    lines = [r.getMessage() for r in caplog.records if "kind=agent:scenario" in r.getMessage()]
    assert len(lines) == 2
    assert "lines=10" in lines[0] and "tokens=" in lines[0] and "failure" not in lines[0]
    assert "failure=analysis_failed" in lines[1]
    assert all("merge_results" not in r.getMessage() for r in caplog.records)


def test_output_token_caps_are_set():
    from server.intake import check_code

    assert analyst.build_analyst(check_code(AGENT_TOOLS), "work").model_settings.max_tokens
    assert judge.build_judge().model_settings.max_tokens
    assert coach._agent("x", "y", dict).model_settings.max_tokens


def test_oversized_body_is_rejected(client):
    response = client.post(
        "/api/scan",
        content=b"{" + b" " * MAX_BODY_BYTES + b"}",
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "input_invalid"


def test_too_many_steps_is_input_invalid(client):
    step = {"id": 1, "title": "t", "symbol": "s", "lines": [1], "summary": "s"}
    response = client.post("/api/scenario", json={**SCENARIO_BODY, "steps": [step] * 9})
    assert response.status_code == 400
