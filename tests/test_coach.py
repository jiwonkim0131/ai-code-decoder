"""코치 POST /api/scenario, /api/feedback(BE-08)."""

from __future__ import annotations

import pytest

from tests.fakes import AGENT_TOOLS, constraint, constraints_draft, final, input_text, step

CONSTRAINT = constraint()

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


def test_scenario_uses_constraint_code_and_steps(client, scripted_model):
    scripted_model.extend([final(SCENARIO)])

    response = client.post(
        "/api/scenario", json={"code": AGENT_TOOLS, "constraint": CONSTRAINT, "steps": STEPS}
    )

    assert response.status_code == 200
    assert response.json() == {
        "constraintId": "k1",
        "question": SCENARIO["question"],
        "followUp": SCENARIO["follow_up"],
        "hint": SCENARIO["hint"],
    }
    text = input_text(scripted_model.first_call)
    assert f"<constraint>\n{CONSTRAINT['label']}: {CONSTRAINT['situation']}" in text
    assert "걸리는 곳: asyncio.gather (9번째 줄)" in text
    assert f"깨지는 가정: {CONSTRAINT['assumption']}" in text
    assert f"중요한 이유: {CONSTRAINT['rationale']}" in text
    assert " 9|     results = await asyncio.gather(" in text
    assert "2. 병렬 호출 (asyncio.gather, 9번째 줄)" in text


@pytest.mark.parametrize("question", ["어떻게 될까요? 왜 그럴까요?", "어떻게 될지 설명해 주세요."])
def test_scenario_question_must_have_one_question_mark_at_end(client, scripted_model, question):
    scripted_model.extend([final({**SCENARIO, "question": question}), final(SCENARIO)])

    response = client.post("/api/scenario", json={"code": AGENT_TOOLS, "constraint": CONSTRAINT})

    assert response.status_code == 200
    assert "물음표가 문장 끝에 하나만" in input_text(scripted_model.last_call)


def test_scenario_fails_after_second_bad_output(client, scripted_model):
    bad = final({**SCENARIO, "question": "a? b?"})
    scripted_model.extend([bad, bad])

    response = client.post("/api/scenario", json={"code": AGENT_TOOLS, "constraint": CONSTRAINT})

    assert response.status_code == 502


def test_feedback_returns_points_and_further(client, scripted_model):
    scripted_model.extend([final(FEEDBACK)])
    body = {
        "code": AGENT_TOOLS,
        "constraint": CONSTRAINT,
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
    body = {"code": AGENT_TOOLS, "constraint": CONSTRAINT, "question": "q?", "answer": "a"}

    assert client.post("/api/feedback", json=body).status_code == 200
    assert "3개 이하" in input_text(scripted_model.last_call)


def test_model_answer_is_made_on_request(client, scripted_model):
    scripted_model.extend([final(MODEL_ANSWER)])
    body = {"code": AGENT_TOOLS, "constraint": CONSTRAINT, "question": SCENARIO["question"]}

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

    response = client.post("/api/scenario", json={"code": AGENT_TOOLS, "constraint": CONSTRAINT})

    assert response.status_code == 200
    assert message in input_text(scripted_model.last_call)


def test_model_answer_needs_three_to_five_sentences(client, scripted_model):
    scripted_model.extend([final({"answer": "한 문장뿐이에요."}), final(MODEL_ANSWER)])
    body = {"code": AGENT_TOOLS, "constraint": CONSTRAINT, "question": SCENARIO["question"]}

    assert client.post("/api/model-answer", json=body).status_code == 200
    assert "3~5문장" in input_text(scripted_model.last_call)


def test_scenario_question_must_not_repeat_situation(client, scripted_model):
    repeated = f"{CONSTRAINT['situation'].rstrip('.')}. run_tools는 어떻게 될까요?"
    scripted_model.extend([final({**SCENARIO, "question": repeated}), final(SCENARIO)])

    response = client.post("/api/scenario", json={"code": AGENT_TOOLS, "constraint": CONSTRAINT})

    assert response.status_code == 200
    assert "상황 설명(situation)을 그대로 옮겼습니다" in input_text(scripted_model.last_call)


def test_preset_name_is_input_invalid(client):
    response = client.post("/api/scenario", json={"code": AGENT_TOOLS, "preset": "latency"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "input_invalid"


def test_constraint_outside_code_is_input_invalid(client, scripted_model):
    body = {"code": AGENT_TOOLS, "constraint": constraint(lines=[99])}

    response = client.post("/api/scenario", json=body)

    assert response.status_code == 400
    assert response.json()["error"]["details"]["fields"][0]["field"] == "constraint.lines"
    assert not scripted_model.calls


# --- POST /api/constraints ---------------------------------------------


def _constraints(client, **body):
    return client.post("/api/constraints", json={"code": AGENT_TOOLS, **body})


def test_constraints_use_code_and_steps(client, scripted_model):
    scripted_model.extend([final(constraints_draft(constraint()))])

    response = _constraints(client, steps=[step(2, "asyncio.gather", [9])])

    assert response.status_code == 200
    assert response.json() == {"constraints": [constraint()]}
    text = input_text(scripted_model.first_call)
    assert " 9|     results = await asyncio.gather(" in text
    assert "2. 2단계 (asyncio.gather, 9번째 줄)" in text


def test_no_constraints_is_valid(client, scripted_model):
    # 바꿔 볼 제약이 없는 코드면 빈 목록이 정상이다(FE가 설계 응용을 건너뛴다).
    scripted_model.extend([final(constraints_draft())])

    response = _constraints(client)

    assert response.status_code == 200
    assert response.json() == {"constraints": []}
    assert len(scripted_model.calls) == 1


@pytest.mark.parametrize(
    "bad,message",
    [
        (constraint(symbol="redis.get"), "symbol 'redis.get'이 코드에 없습니다"),
        (constraint(lines=[42]), "42번째 줄은 입력에 없습니다"),
        (constraint(assumption=" "), "assumption이 비어 있습니다"),
        (constraint(label="tool 하나가 느려지면 어떻게 될까요?"), "버튼 이름입니다"),
        (constraint(label="팀원 세 명이 이 코드를 함께 나눠서 고치고 운영함"), "24자 이내"),
        (constraint(situation="응답이 느려요. 어떻게 될까요?"), "물음표 없는 한 문장"),
        (constraint(situation="call_tool 응답 3초 지연."), "label과 같습니다"),
        (constraint(situation="tool 하나가 3초 뒤에 응답한다."), "'~해요' 말투로 끝내세요"),
        (constraint(situation="tool 하나가 느려져 타임아웃될 수 있어요."), "결과 표현(수 있어요)"),
    ],
)
def test_constraint_rules_are_sent_back(client, scripted_model, bad, message):
    scripted_model.extend([final(constraints_draft(bad)), final(constraints_draft(constraint()))])

    response = _constraints(client)

    assert response.status_code == 200
    assert message in input_text(scripted_model.last_call)


def test_constraint_outside_operational_lines_is_sent_back(client, scripted_model):
    # 외부 호출이 없는 줄(6번째 줄 함수 정의)에 건 제약은 근거 없는 제약으로 본다.
    draft = constraints_draft(constraint(lines=[6], symbol="run_tools"))
    scripted_model.extend([final(draft), final(draft)])

    response = _constraints(client)

    assert "operational_lines [7, 8, 9, 10]에 없습니다" in input_text(scripted_model.last_call)
    assert response.json() == {"constraints": []}


def test_code_without_operational_calls_skips_the_model(client, scripted_model):
    code = "def total(items):\n    return round(sum(items), 2)\n"

    response = client.post("/api/constraints", json={"code": code})

    assert response.json() == {"constraints": []}
    assert not scripted_model.calls


def test_label_repeating_a_value_in_code_is_sent_back(client, scripted_model):
    # timeout=5가 적힌 줄에 "5초 타임아웃"을 붙이면 바뀐 조건이 아니라 코드 설명이다.
    code = "import requests\n\nr = requests.get('https://x', timeout=5)\n"
    bad = constraint(lines=[3], symbol="requests.get", label="API 호출 5초 타임아웃")
    good = constraint(lines=[3], symbol="requests.get", label="API 응답 10초 지연")
    scripted_model.extend([final(constraints_draft(bad)), final(constraints_draft(good))])

    response = client.post("/api/constraints", json={"code": code})

    assert [c["label"] for c in response.json()["constraints"]] == ["API 응답 10초 지연"]
    assert "5은 코드에 이미 적힌 값입니다" in input_text(scripted_model.last_call)


def test_ungrounded_constraint_left_after_retry_is_dropped(client, scripted_model):
    # 고친 출력에도 근거 없는 제약이 남으면 실패시키지 않고 그 제약만 뺀다.
    draft = constraints_draft(constraint(symbol="redis.get"), constraint(id="k2"))
    scripted_model.extend([final(draft), final(draft)])

    response = _constraints(client)

    assert response.status_code == 200
    assert [c["id"] for c in response.json()["constraints"]] == ["k2"]


def test_constraint_wording_left_after_retry_is_dropped(client, scripted_model):
    # 문구 규칙도 고친 출력에서 여전히 어기면 그 제약만 빼고, 나머지는 받는다.
    draft = constraints_draft(constraint(label="call_tool 응답 지연?"), constraint(id="k2"))
    scripted_model.extend([final(draft), final(draft)])

    response = _constraints(client)

    assert response.status_code == 200
    assert [c["id"] for c in response.json()["constraints"]] == ["k2"]


def test_duplicate_and_extra_constraints_are_trimmed(client, scripted_model):
    many = [constraint(id=f"k{i}", label=f"call_tool 응답 {i}초 지연") for i in range(1, 7)]
    many.insert(1, constraint(id="k1", label="call_tool 응답 9초 지연"))  # id 중복
    scripted_model.extend([final(constraints_draft(*many)), final(constraints_draft(*many))])

    body = _constraints(client).json()

    assert [c["id"] for c in body["constraints"]] == ["k1", "k2", "k3", "k4", "k5"]
