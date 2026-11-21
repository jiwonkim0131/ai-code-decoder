"""테스트용 가짜 값: 목업 사례 코드, 모델 최종 출력 JSON, 문서 조회 결과."""

from __future__ import annotations

import json
from typing import Any

from agents.testing import assistant_message, function_call

from server.docs.documentation import DocumentationEvidence

# 목업 사례(agent_tools.py)와 같은 모양. 9번째 줄이 asyncio.gather다.
AGENT_TOOLS = """\
import asyncio

from tools import call_tool, merge_results, plan_tools


async def run_tools(question):
    calls = plan_tools(question)
    tasks = [call_tool(name, args) for name, args in calls]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    return merge_results(results)
"""

GATHER_URL = "https://docs.python.org/3/library/asyncio-task.html#asyncio.gather"


def doc_evidence(module: str, symbol: str, status: str, url: str = "") -> DocumentationEvidence:
    return DocumentationEvidence(
        source_id="source-test",
        status=status,
        module=module,
        symbol=symbol,
        title=f"Python documentation: {module}.{symbol}",
        excerpt="excerpt",
        url=url or f"https://docs.python.org/3/library/{module}.html",
    )


def step(id: int, symbol: str, lines: list[int], evidence: list[dict] | None = None, **kw) -> dict:
    return {
        "id": id,
        "title": kw.get("title", f"{id}단계"),
        "symbol": symbol,
        "lines": lines,
        "summary": kw.get("summary", "설명"),
        "io": kw.get("io"),
        "evidence": evidence
        if evidence is not None
        else [{"badge": "inferred", "symbol": symbol, "note": "이름에서 추정"}],
    }


def decision_point(**overrides: Any) -> dict:
    data = {
        "id": "dp1",
        "step_id": 2,
        "lines": [9],
        "prompt": "너라면 tool 여러 개를 어떻게 실행하겠어요?",
        "options": [
            {"id": "A", "label": "for 루프로 순차 실행", "detail": "하나씩 기다린다"},
            {"id": "B", "label": "asyncio.gather", "detail": "동시에 실행하고 모두 기다린다"},
            {
                "id": "C",
                "label": "asyncio.TaskGroup",
                "detail": "하나가 실패하면 나머지를 취소한다",
            },
            {"id": "D", "label": "asyncio.wait(FIRST_COMPLETED)", "detail": "먼저 끝난 것부터"},
        ],
        "code_option": "B",
        "hint": "tool 하나가 실패하면 나머지 결과는 어떻게 돼야 할까요?",
        "conditions": [
            {
                "id": "c1",
                "label": "전체 대기 시간",
                "explanation": "동시에 실행하면 가장 느린 tool만큼만 기다린다",
                "splits": ["B", "C"],
            },
            {
                "id": "c2",
                "label": "부분 실패 허용",
                "explanation": "실패한 tool이 있어도 나머지 결과를 써야 하는가",
                "splits": ["B"],
            },
        ],
    }
    data.update(overrides)
    return data


def analysis_draft(**overrides: Any) -> dict:
    data = {
        "steps": [
            step(1, "plan_tools", [7, 8]),
            step(
                2,
                "asyncio.gather",
                [9],
                [{"badge": "official", "symbol": "asyncio.gather", "note": ""}],
            ),
            step(3, "merge_results", [10]),
        ],
        "decision_points": [decision_point()],
    }
    data.update(overrides)
    # 후보를 따로 주지 않으면 결정 지점마다 후보 하나로 맞춘다(개수 검증을 통과하는 기본값).
    data.setdefault(
        "decision_candidates",
        [{"topic": dp["prompt"], "lines": dp["lines"]} for dp in data["decision_points"]],
    )
    return data


def final(payload: dict) -> list:
    """모델의 최종 출력 한 턴."""
    return [assistant_message(json.dumps(payload, ensure_ascii=False))]


def lookup(module: str, symbol: str, call_id: str = "call-1") -> list:
    """모델의 lookup_docs 호출 한 턴."""
    return [function_call("lookup_docs", {"module": module, "symbol": symbol}, call_id=call_id)]


def input_text(call) -> str:
    """모델 호출 입력(문자열 또는 메시지 목록)을 텍스트 하나로."""
    if isinstance(call.input, str):
        return call.input
    return "\n".join(str(item.get("content", item)) for item in call.input)


def api_decision_point() -> dict:
    """FE가 analyze 응답에서 받아 judge에 다시 보내는 결정 지점(camelCase)."""
    dp = decision_point()
    return {
        "id": dp["id"],
        "stepId": dp["step_id"],
        "lines": dp["lines"],
        "prompt": dp["prompt"],
        "options": dp["options"],
        "codeOption": dp["code_option"],
        "hint": dp["hint"],
        "conditions": dp["conditions"],
    }


def judge_draft(statuses: dict[str, str | None] | None = None, **overrides: Any) -> dict:
    statuses = statuses if statuses is not None else {"c1": "hit", "c2": "miss"}
    data = {
        "conditions": [
            {
                "id": cid,
                "quote": "실패해도 계속" if status == "hit" else "",
                "status": status,
                "note": f"{cid} 설명",
            }
            for cid, status in statuses.items()
        ],
        "code_choice": {
            "summary": "부분 실패를 허용하려고",
            "intent_note": "return_exceptions에서",
        },
        "tradeoffs": [
            {"option_id": oid, "wait_time": "w", "on_failure": "f", "complexity": "c"}
            for oid in "DCBA"  # 순서는 서버가 정렬한다
        ],
    }
    data.update(overrides)
    return data
