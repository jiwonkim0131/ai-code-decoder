from __future__ import annotations

import logging

import pytest
from pydantic import ValidationError

from server.logs import format_log_fields
from server.models import (
    MAX_CODE_CHARACTERS,
    AnalyzeRequest,
    DecisionPoint,
    Evidence,
    Step,
)


def _decision_point(**overrides) -> dict:
    data = {
        "id": "dp1",
        "stepId": 2,
        "lines": [9],
        "prompt": "너라면 tool 3개를 어떻게 실행할래요?",
        "options": [{"id": i, "label": f"option {i}"} for i in "ABCD"],
        "hint": "tool 하나가 실패하면?",
        "conditions": [
            {"id": "c1", "label": "전체 대기 시간", "explanation": "...", "splits": ["A", "B"]}
        ],
    }
    data.update(overrides)
    return data


# --- 모델 ---------------------------------------------------------------


def test_analyze_request_defaults_and_camel_case():
    req = AnalyzeRequest.model_validate({"code": "x = 1", "focusLines": [1]})
    assert req.level == "work"
    assert req.language == "python"
    assert req.focus_lines == [1]


def test_unknown_fields_are_rejected():
    with pytest.raises(ValidationError):
        AnalyzeRequest.model_validate({"code": "x = 1", "context": "확장 필드"})


def test_official_evidence_requires_url():
    with pytest.raises(ValidationError):
        Evidence(badge="official", symbol="asyncio.gather")
    assert Evidence(badge="needs_check", note="확인 필요").url is None


def test_step_serializes_camel_case_and_io_alias():
    step = Step.model_validate(
        {
            "id": 1,
            "title": "도구 계획",
            "symbol": "plan_tools",
            "lines": [7, 8],
            "summary": "...",
            "io": {"in": "질문", "out": "(tool 이름, 인자) 목록"},
            "isDecision": False,
        }
    )
    dumped = step.model_dump(by_alias=True)
    assert dumped["io"] == {"in": "질문", "out": "(tool 이름, 인자) 목록"}
    assert "isDecision" in dumped


def test_decision_point_requires_four_options():
    DecisionPoint.model_validate(_decision_point())
    with pytest.raises(ValidationError):
        DecisionPoint.model_validate(_decision_point(options=[{"id": "A", "label": "a"}]))


# --- 오류 형식 ----------------------------------------------------------


def test_validation_error_is_400_input_invalid_without_echoing_code(client):
    secret_like_code = "x" * (MAX_CODE_CHARACTERS + 1)
    response = client.post("/api/analyze", json={"code": secret_like_code})

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "input_invalid"
    assert error["details"]["fields"][0]["field"] == "code"
    assert secret_like_code not in response.text


def test_unimplemented_route_uses_error_shape(client):
    response = client.post("/api/analyze", json={"code": "x = 1"})
    assert response.status_code == 501
    assert response.json() == {
        "error": {"code": "not_implemented", "message": "아직 구현되지 않았어요(BE-06)."}
    }


def test_unknown_route_uses_error_shape(client):
    response = client.get("/api/nope")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_judge_accepts_null_choice_for_skip(client):
    response = client.post("/api/judge", json={"decisionPoint": _decision_point(), "choice": None})
    assert response.status_code == 501  # 계약 통과, 구현은 BE-07


# --- 로그 규칙 ----------------------------------------------------------


def test_log_fields_are_whitelisted():
    assert format_log_fields(kind="analyze", lines=16, tokens=None) == "kind=analyze lines=16"
    with pytest.raises(ValueError):
        format_log_fields(code="import os")


def test_request_log_has_no_body(client, caplog: pytest.LogCaptureFixture):
    marker = "UNIQUE_CODE_MARKER_42"
    with caplog.at_level(logging.INFO):
        client.post("/api/analyze", json={"code": f"print('{marker}')"})
    assert any("POST /api/analyze" in r.getMessage() for r in caplog.records)
    assert all(marker not in r.getMessage() for r in caplog.records)


# --- OpenAPI 계약 -------------------------------------------------------


def test_openapi_has_all_endpoints_and_no_dev_routes(client):
    paths = client.get("/openapi.json").json()["paths"]
    for path in ("/api/scan", "/api/analyze", "/api/judge", "/api/scenario", "/api/feedback"):
        assert path in paths
    assert not any(p.startswith("/api/dev") for p in paths)


def test_openapi_documents_400_not_422(client):
    analyze = client.get("/openapi.json").json()["paths"]["/api/analyze"]["post"]
    assert "400" in analyze["responses"]
    assert "422" not in analyze["responses"]
