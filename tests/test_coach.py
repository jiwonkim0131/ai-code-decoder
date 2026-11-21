"""코치 POST /api/scenario, /api/feedback(BE-08)."""

from __future__ import annotations

import pytest

from tests.fakes import AGENT_TOOLS, final, input_text

SCENARIO = {
    "question": "call_tool 하나가 3초 걸리면 run_tools 전체는 얼마나 걸릴까요?",
    "follow_up": "9번째 줄의 gather가 무엇을 기다리는지 보세요.",
    "hint": "가장 느린 호출이 전체 시간을 정하는지 떠올려 보세요.",
}
MODEL_ANSWER = {
    "answer": "gather는 모든 호출이 끝날 때까지 기다려요. 그래서 전체가 3초 이상 걸려요. "
    "타임아웃을 두면 느린 호출을 끊을 수 있어요."
}
FEEDBACK = {
    "verdict": "대기 시간을 잘 짚었어요.",
    "points": ["가장 느린 tool만큼 기다린다는 점"],
    "further": "타임아웃이 없다는 점도 생각해 보세요.",
}
STEPS = [
    {
        "id": 2,
        "title": "병렬 호출",
        "symbol": "asyncio.gather",
        "lines": [9],
        "summary": "동시에 실행",
    }
]


@pytest.mark.parametrize(
    "preset,name",
    [
        ("latency", "tool 하나 3초 지연"),
        ("rateLimit", "rate limit 분당 60회"),
        ("traffic", "트래픽 10배"),
        ("team", "팀 3명"),
    ],
)
def test_scenario_uses_preset_code_and_steps(client, scripted_model, preset, name):
    scripted_model.extend([final(SCENARIO)])

    response = client.post(
        "/api/scenario", json={"code": AGENT_TOOLS, "preset": preset, "steps": STEPS}
    )

    assert response.status_code == 200
    assert response.json() == {
        "preset": preset,
        "question": SCENARIO["question"],
        "followUp": SCENARIO["follow_up"],
        "hint": SCENARIO["hint"],
    }
    text = input_text(scripted_model.first_call)
    assert f"<preset>{name}:" in text
    assert " 9|     results = await asyncio.gather(" in text
    assert "2. 병렬 호출 (asyncio.gather, 9번째 줄)" in text


@pytest.mark.parametrize("question", ["어떻게 될까요? 왜 그럴까요?", "어떻게 될지 설명해 주세요."])
def test_scenario_question_must_have_one_question_mark_at_end(client, scripted_model, question):
    scripted_model.extend([final({**SCENARIO, "question": question}), final(SCENARIO)])

    response = client.post("/api/scenario", json={"code": AGENT_TOOLS, "preset": "latency"})

    assert response.status_code == 200
    assert "물음표가 문장 끝에 하나만" in input_text(scripted_model.last_call)


def test_scenario_fails_after_second_bad_output(client, scripted_model):
    bad = final({**SCENARIO, "question": "a? b?"})
    scripted_model.extend([bad, bad])

    response = client.post("/api/scenario", json={"code": AGENT_TOOLS, "preset": "latency"})

    assert response.status_code == 502


def test_feedback_returns_points_and_further(client, scripted_model):
    scripted_model.extend([final(FEEDBACK)])
    body = {
        "code": AGENT_TOOLS,
        "preset": "latency",
        "question": SCENARIO["question"],
        "answer": "가장 느린 것만큼 기다려요",
    }

    response = client.post("/api/feedback", json=body)

    assert response.status_code == 200
    assert response.json() == FEEDBACK
    text = input_text(scripted_model.first_call)
    assert "<answer>\n가장 느린 것만큼 기다려요\n</answer>" in text


def test_feedback_with_too_many_points_is_corrected(client, scripted_model):
    scripted_model.extend([final({**FEEDBACK, "points": ["a", "b", "c", "d"]}), final(FEEDBACK)])
    body = {"code": AGENT_TOOLS, "preset": "team", "question": "q?", "answer": "a"}

    assert client.post("/api/feedback", json=body).status_code == 200
    assert "3개 이하" in input_text(scripted_model.last_call)


def test_model_answer_is_made_on_request(client, scripted_model):
    scripted_model.extend([final(MODEL_ANSWER)])
    body = {"code": AGENT_TOOLS, "preset": "latency", "question": SCENARIO["question"]}

    response = client.post("/api/model-answer", json=body)

    assert response.status_code == 200
    assert response.json() == MODEL_ANSWER
    assert SCENARIO["question"] in input_text(scripted_model.first_call)


@pytest.mark.parametrize(
    "override,message",
    [
        ({"hint": "첫째 문장이에요. 둘째 문장이에요. 셋째 문장이에요."}, "hint는 2문장 이하"),
        ({"follow_up": "9번째 줄을 보세요. 그리고 10번째 줄도 보세요."}, "한 문장"),
        ({"hint": "9번째 줄을 확인합니다."}, "'~습니다' 말투"),
    ],
)
def test_scenario_style_is_sent_back_once(client, scripted_model, override, message):
    # 문장 수·말투는 첫 출력에서만 돌려보내고, 고친 출력이 여전히 어겨도 받는다.
    bad = final({**SCENARIO, **override})
    scripted_model.extend([bad, bad])

    response = client.post("/api/scenario", json={"code": AGENT_TOOLS, "preset": "latency"})

    assert response.status_code == 200
    assert message in input_text(scripted_model.last_call)


def test_model_answer_needs_three_to_five_sentences(client, scripted_model):
    scripted_model.extend([final({"answer": "한 문장뿐이에요."}), final(MODEL_ANSWER)])
    body = {"code": AGENT_TOOLS, "preset": "latency", "question": SCENARIO["question"]}

    assert client.post("/api/model-answer", json=body).status_code == 200
    assert "3~5문장" in input_text(scripted_model.last_call)


def test_unknown_preset_is_input_invalid(client):
    response = client.post("/api/scenario", json={"code": AGENT_TOOLS, "preset": "cost"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "input_invalid"
