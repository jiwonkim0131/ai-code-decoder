from __future__ import annotations

from types import SimpleNamespace

import pytest

import server.dev as dev


def test_health(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_dev_routes_are_off_by_default(client):
    assert client.post("/api/dev/agent-ping").status_code == 404


def test_agent_ping_requires_key(make_client):
    client = make_client(ENABLE_DEV_ROUTES="true")
    assert client.post("/api/dev/agent-ping").status_code == 503


def test_agent_ping_reports_duration_and_tokens(make_client, monkeypatch: pytest.MonkeyPatch):
    async def fake_run(agent, user_input, **kwargs):
        assert kwargs["max_turns"] == 1
        usage = SimpleNamespace(total_tokens=12)
        return SimpleNamespace(final_output="pong", context_wrapper=SimpleNamespace(usage=usage))

    monkeypatch.setattr(dev.Runner, "run", fake_run)
    client = make_client(ENABLE_DEV_ROUTES="true", OPENAI_API_KEY="test-key", OPENAI_MODEL="m")

    body = client.post("/api/dev/agent-ping").json()

    assert body["ok"] is True
    assert body["model"] == "m"
    assert body["output"] == "pong"
    assert body["totalTokens"] == 12
    assert body["durationMs"] >= 0
