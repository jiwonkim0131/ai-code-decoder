"""학습 정리 POST /api/summary."""

from __future__ import annotations

import pytest

from tests.fakes import decision_point, final, input_text, step

VERDICT = {
    "skipped": False,
    "hit": 1,
    "total": 2,
    "conditions": [
        {"id": "c1", "status": "hit", "note": "대기 시간을 짚었어요"},
        {"id": "c2", "status": "miss", "note": "부분 실패를 다루지 않았어요"},
    ],
    "mine": {"choice": "C", "reason": "실패하면 바로 멈추고 싶어서"},
    "code_choice": {
        "label": "asyncio.gather",
        "summary": "부분 실패를 허용해요",
        "intent": {"badge": "inferred", "note": "return_exceptions=True에서 추정"},
    },
    "tradeoffs": {
        "rows": [
            {
                "approach": "asyncio.gather",
                "wait_time": "가장 느린 것만큼",
                "on_failure": "예외를 결과로",
                "complexity": "낮음",
                "is_this_code": True,
            }
        ],
        "evidence": {"badge": "inferred", "note": "코드에서 추정"},
    },
}
BODY = {
    "steps": [step(1, "plan_tools", [7, 8]), step(2, "asyncio.gather", [9])],
    "decisions": [{"decisionPoint": decision_point(), "verdict": VERDICT}],
    "applications": [
        {
            "preset": "latency",
            "question": "tool 하나가 3초 걸리면 어떻게 될까요?",
            "answer": "전체가 3초 걸려요",
            "feedback": {"verdict": "좋아요", "points": [], "further": "타임아웃도 보세요."},
        }
    ],
}
SUMMARY = {
    "takeaways": [
        {"text": "9줄 gather는 가장 느린 호출만큼 기다려요.", "refs": ["step:2"]},
        {"text": "return_exceptions=True라 실패해도 나머지 결과를 써요.", "refs": ["decision:dp1"]},
        {"text": "지연이 큰 tool에는 타임아웃이 필요해요.", "refs": ["apply:latency", "step:2"]},
    ],
    "review_points": ["부분 실패 허용 기준을 다시 보세요."],
}


def test_summary_uses_learning_record_and_returns_takeaways(client, scripted_model):
    scripted_model.extend([final(SUMMARY)])

    response = client.post("/api/summary", json=BODY)

    assert response.status_code == 200
    body = response.json()
    assert [t["refs"] for t in body["takeaways"]] == [t["refs"] for t in SUMMARY["takeaways"]]
    assert body["reviewPoints"] == SUMMARY["review_points"]
    text = input_text(scripted_model.first_call)
    assert "[step:2] 2단계 (asyncio.gather, 9번째 줄)" in text
    assert "내 선택: asyncio.TaskGroup (이유: 실패하면 바로 멈추고 싶어서)" in text
    assert "부분 실패 허용: 미반영" in text
    assert "[apply:latency] 조건 tool 하나 3초 지연" in text


def test_summary_refs_must_exist_in_the_record(client, scripted_model):
    bad = {**SUMMARY, "takeaways": [{**SUMMARY["takeaways"][0], "refs": ["decision:dp9"]}]}
    scripted_model.extend([final(bad), final(SUMMARY)])

    assert client.post("/api/summary", json=BODY).status_code == 200
    assert "['decision:dp9']는 입력에 없습니다" in input_text(scripted_model.last_call)


def test_summary_fails_when_refs_stay_wrong(client, scripted_model):
    bad = final({**SUMMARY, "takeaways": [{"text": "a", "refs": []}]})
    scripted_model.extend([bad, bad])

    assert client.post("/api/summary", json=BODY).status_code == 502


@pytest.mark.parametrize(
    "override,message",
    [
        ({"takeaways": SUMMARY["takeaways"][:1]}, "3~5개"),
        ({"review_points": ["다시 봅니다."]}, "'~습니다' 말투"),
    ],
)
def test_summary_style_is_sent_back_once(client, scripted_model, override, message):
    bad = final({**SUMMARY, **override})
    scripted_model.extend([bad, bad])

    assert client.post("/api/summary", json=BODY).status_code == 200
    assert message in input_text(scripted_model.last_call)


def test_summary_works_with_analysis_only(client, scripted_model):
    scripted_model.extend([final({**SUMMARY, "takeaways": SUMMARY["takeaways"][:1] * 3})])

    response = client.post("/api/summary", json={"steps": BODY["steps"]})

    assert response.status_code == 200
    assert "<decisions>\n없음\n</decisions>" in input_text(scripted_model.first_call)
