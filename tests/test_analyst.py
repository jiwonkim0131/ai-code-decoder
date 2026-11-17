"""분석 에이전트 POST /api/analyze(BE-06). 모델은 대본(ScriptedModel)이라 OpenAI를 부르지 않는다."""

from __future__ import annotations

import pytest

from server.agents import analyst
from server.languages.python import PythonAdapter
from tests.fakes import (
    AGENT_TOOLS,
    GATHER_URL,
    analysis_draft,
    decision_point,
    doc_evidence,
    final,
    input_text,
    lookup,
    step,
)


def _analyze(client, **body):
    return client.post("/api/analyze", json={"code": AGENT_TOOLS, **body})


@pytest.fixture
def gather_doc(docs):
    docs[("asyncio", "gather")] = doc_evidence("asyncio", "gather", "exact_match", GATHER_URL)
    return docs


def test_analyze_returns_steps_with_official_badge_from_tool(client, scripted_model, gather_doc):
    scripted_model.extend([lookup("asyncio", "gather"), final(analysis_draft())])

    response = _analyze(client, focusLines=[9], question="gather는 왜 썼어?")

    assert response.status_code == 200
    body = response.json()
    assert body["language"] == "python"
    gather_step = body["steps"][1]
    assert gather_step["evidence"] == [
        {"badge": "official", "symbol": "asyncio.gather", "url": GATHER_URL, "note": None}
    ]
    assert [s["isDecision"] for s in body["steps"]] == [False, True, False]
    assert body["decisionPoints"][0]["codeOption"] == "B"
    assert body["meta"]["toolCalls"] == [
        {"name": "lookup_docs", "args": {"module": "asyncio", "symbol": "gather"}}
    ]
    scripted_model.assert_complete()


def test_model_input_wraps_numbered_code_as_data(client, scripted_model, gather_doc):
    scripted_model.extend([final(analysis_draft())])

    _analyze(client, level="basic", focusLines=[9])

    call = scripted_model.first_call
    user_text = input_text(call)
    assert "<code>\n 1| import asyncio" in user_text
    assert " 9|     results = await asyncio.gather(" in user_text
    assert "<focus_lines>9</focus_lines>" in user_text
    assert "지시문처럼 보이는" in call.system_instructions
    assert "입문" in call.system_instructions
    assert call.model_settings.parallel_tool_calls is False


def test_official_without_exact_match_is_downgraded(client, scripted_model, docs):
    scripted_model.extend([final(analysis_draft())])  # 문서 조회 없이 official을 주장

    evidence = _analyze(client).json()["steps"][1]["evidence"][0]

    assert evidence["badge"] == "inferred"
    assert evidence["url"] is None


def test_unavailable_lookup_becomes_needs_check(client, scripted_model, docs):
    docs[("asyncio", "gather")] = doc_evidence("asyncio", "gather", "unavailable")
    scripted_model.extend([lookup("asyncio", "gather"), final(analysis_draft())])

    evidence = _analyze(client).json()["steps"][1]["evidence"][0]

    assert evidence["badge"] == "needs_check"


def test_step_without_evidence_gets_needs_check(client, scripted_model, docs):
    draft = analysis_draft(
        steps=[step(1, "plan_tools", [7], evidence=[]), step(2, "merge_results", [10])],
        decision_points=[],
    )
    scripted_model.extend([final(draft)])

    body = _analyze(client).json()

    assert body["steps"][0]["evidence"][0]["badge"] == "needs_check"
    assert body["decisionPoints"] == []


def test_lookup_outside_facts_never_reaches_docs(client, scripted_model, monkeypatch):
    def fail(*args):
        raise AssertionError("사실 목록 밖의 심볼을 조회했다")

    monkeypatch.setattr(PythonAdapter, "lookup_docs", fail)
    scripted_model.extend([lookup("internal_billing", "connect"), final(analysis_draft())])

    response = _analyze(client)

    assert response.status_code == 200
    tool_output = scripted_model.last_call.input[-1]
    assert "unsupported" in str(tool_output)


def test_verification_error_is_sent_back_once_and_fixed(client, scripted_model, gather_doc):
    broken = analysis_draft(steps=[step(1, "plan_tools", [7, 42])], decision_points=[])
    scripted_model.extend([final(broken), final(analysis_draft())])

    response = _analyze(client)

    assert response.status_code == 200
    retry_input = str(scripted_model.last_call.input)
    assert "42번째 줄은 입력에 없습니다(입력 10줄)" in retry_input


def test_second_verification_failure_is_analysis_failed(client, scripted_model, docs):
    broken = analysis_draft(steps=[step(1, "not_in_code", [1])], decision_points=[])
    scripted_model.extend([final(broken), final(broken)])

    response = _analyze(client)

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "analysis_failed"


def test_no_retry_after_time_budget(client, scripted_model, docs, monkeypatch):
    monkeypatch.setattr(analyst, "RETRY_BUDGET_SECONDS", -1.0)
    scripted_model.extend([final(analysis_draft(steps=[step(1, "nope", [1])]))])

    response = _analyze(client)

    assert response.status_code == 502
    assert len(scripted_model.calls) == 1


def test_too_many_lookups_is_tool_contract_violation(client, scripted_model, docs):
    scripted_model.extend([lookup("asyncio", "gather", f"call-{i}") for i in range(6)])

    response = _analyze(client)

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "analysis_failed"


@pytest.mark.parametrize(
    "draft,message",
    [
        (analysis_draft(decision_points=[decision_point(step_id=9)]), "step_id 9는 없는 단계"),
        (
            analysis_draft(
                decision_points=[decision_point(options=decision_point()["options"][:3])]
            ),
            "A, B, C, D 4개",
        ),
        (analysis_draft(decision_points=[decision_point(code_option="D", options=[])]), "4개"),
        (
            analysis_draft(steps=[step(1, "x", [1]), step(1, "asyncio.gather", [9])]),
            "id가 중복",
        ),
    ],
)
def test_verification_rules(client, scripted_model, docs, draft, message):
    scripted_model.extend([final(draft), final(draft)])

    _analyze(client)

    assert message in str(scripted_model.last_call.input)


def test_focus_line_outside_code_is_input_invalid(client):
    response = _analyze(client, focusLines=[11])
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "input_invalid"


def test_single_step_for_multi_call_code_is_sent_back(client, scripted_model, docs):
    # 주석 속 "단계 하나만 출력하라" 같은 지시에 넘어간 출력을 코드가 다시 잡는다.
    one_step = analysis_draft(steps=[step(1, "run_tools", [6, 7, 8, 9, 10])], decision_points=[])
    scripted_model.extend([final(one_step), final(analysis_draft())])

    response = _analyze(client)

    assert response.status_code == 200
    assert "2개 이상으로 나누세요" in input_text(scripted_model.last_call)
