"""코드를 받는 엔드포인트의 입구 검사: POST /api/scan, 허용 언어, 비밀값, 문법 오류."""

from __future__ import annotations

import logging

import pytest

from server.secrets import REDACTED

OPENAI_KEY = "sk-" + "proj-" + "Ab3dEf6hIj9lMn2pQr5tUv8xYz1bCd4fGh7"
CODE_WITH_KEY = f'import os\n\nclient_key = "{OPENAI_KEY}"\n'


def test_scan_returns_findings_with_positions(client):
    response = client.post("/api/scan", json={"code": CODE_WITH_KEY})

    assert response.status_code == 200
    [finding] = response.json()["findings"]
    assert finding["line"] == 3
    assert CODE_WITH_KEY.split("\n")[2][finding["start"] : finding["end"]] == OPENAI_KEY
    assert finding["kind"] == "openai_api_key"
    assert OPENAI_KEY not in response.text


def test_scan_after_redaction_is_clean(client):
    redacted = CODE_WITH_KEY.replace(OPENAI_KEY, REDACTED)
    assert client.post("/api/scan", json={"code": redacted}).json() == {"findings": []}


@pytest.mark.parametrize(
    "path,extra",
    [
        ("/api/analyze", {}),
        ("/api/scenario", {"preset": "latency"}),
        ("/api/feedback", {"preset": "latency", "question": "q?", "answer": "a"}),
    ],
)
def test_llm_endpoints_reject_code_with_secrets(client, path, extra):
    response = client.post(path, json={"code": CODE_WITH_KEY, **extra})

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "secrets_detected"
    assert error["details"]["findings"][0]["line"] == 3
    assert OPENAI_KEY not in response.text


def test_analyze_reports_syntax_error_line(client):
    response = client.post("/api/analyze", json={"code": "x = 1\nif x\n    pass\n"})

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "syntax_error"
    assert error["details"] == {"line": 2}
    assert "Python 코드만" in error["message"]


def test_secrets_are_checked_before_syntax(client):
    # 문법 오류가 있어도 키가 남아 있으면 먼저 가리게 한다.
    response = client.post("/api/analyze", json={"code": f'key = "{OPENAI_KEY}"\nif x\n'})
    assert response.json()["error"]["code"] == "secrets_detected"


def test_analyze_rejects_unsupported_language(client):
    response = client.post("/api/analyze", json={"code": "x = 1", "language": "javascript"})

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "language_unsupported"
    assert error["details"] == {"supported": ["python"]}


def test_allowed_langs_can_turn_python_off(make_client):
    client = make_client(ALLOWED_LANGS="javascript")
    response = client.post("/api/analyze", json={"code": "x = 1"})
    assert response.json()["error"]["code"] == "language_unsupported"


def test_valid_code_passes_intake(client):
    # 입구를 통과하면 아직 구현 전인 코치(BE-08)에서 501이 난다.
    code = "import asyncio\nasyncio.run(main())\n"
    response = client.post("/api/scenario", json={"code": code, "preset": "latency"})
    assert response.status_code == 501


def test_intake_errors_do_not_log_code(client, caplog: pytest.LogCaptureFixture):
    with caplog.at_level(logging.INFO):
        client.post("/api/analyze", json={"code": CODE_WITH_KEY})
        client.post("/api/scan", json={"code": CODE_WITH_KEY})
    assert all(OPENAI_KEY not in r.getMessage() for r in caplog.records)


def test_dev_lookup_docs_reports_duration(make_client, monkeypatch: pytest.MonkeyPatch):
    from server.docs.documentation import DocumentationEvidence
    from server.languages.python import PythonAdapter

    def fake_lookup(self, module, symbol):
        return DocumentationEvidence(
            source_id="s",
            status="exact_match",
            module=module,
            symbol=symbol,
            title="t",
            excerpt="e",
            url="https://docs.python.org/3/library/asyncio-task.html#asyncio.gather",
        )

    monkeypatch.setattr(PythonAdapter, "lookup_docs", fake_lookup)
    client = make_client(ENABLE_DEV_ROUTES="true")

    body = client.post(
        "/api/dev/lookup-docs", json={"module": "asyncio", "symbol": "gather"}
    ).json()

    assert body["status"] == "exact_match"
    assert body["durationMs"] >= 0
