"""모델 출력 검증과 근거 배지 부여(BE-06).

스터디 레포 `agent/quality.py`의 "실패하면 오류" 형태만 따른다. 검사 항목은 줄 범위, 심볼이
코드에 있는지, 결정 지점 → 단계 참조, 선택지·조건의 모양이다. 오류 문장은 모델에게 그대로
돌려줘 고치게 하므로 무엇이 왜 틀렸는지 구체적으로 쓴다.

근거 배지 정책(설계 문서): official은 lookup_docs가 exact_match를 준 심볼에만, URL은 도구 결과에서
복사한다. 그 밖의 official은 inferred로 낮추고, 문서 조회가 unavailable이면 needs_check,
근거가 하나도 없는 단계에는 needs_check를 붙인다.
"""

from __future__ import annotations

import re

from server.agents.context import AnalystContext
from server.agents.schemas import (
    AnalysisDraft,
    ConstraintsDraft,
    DraftConstraint,
    DraftEvidence,
    DraftStep,
)
from server.intake import CheckedCode
from server.models import (
    MAX_CONSTRAINTS,
    MAX_STEPS,
    AnalyzeMeta,
    AnalyzeResponse,
    Condition,
    Constraint,
    ConstraintsResponse,
    DecisionPoint,
    Evidence,
    Option,
    Step,
    StepIO,
)

MAX_DECISION_POINTS = 3
MIN_CONDITIONS = 2
# 호출이 이만큼 여러 줄에 있으면 단계 하나로 뭉치지 않게 한다(게으른 출력·주석 속 지시 대응)
MIN_CALL_LINES_FOR_SPLIT = 3
OPTION_IDS = ("A", "B", "C", "D")
CONSTRAINT_LABEL_MAX_CHARACTERS = 24
# 상황 설명에 쓰면 사용자가 답할 결과를 미리 말하게 되는 표현
_RESULT_PHRASES = ("수 있어요", "타임아웃돼", "타임아웃이 나", "대기열", "병목")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_SENTENCE_END = re.compile(r"[.!?](?=\s|$)")  # 1.5 같은 숫자의 점은 세지 않는다
# 합니다·됩니다·습니다·입니다처럼 '~니다/~니까'로 끝나는 문장(하십시오체)
FORMAL_ENDING = re.compile(r"니(다|까)[.!?]?(?=\s|$)")

DOWNGRADED_NOTE = "공식 문서에서 이 심볼을 정확히 찾지 못해 추정으로 표시했어요."
UNAVAILABLE_NOTE = "공식 문서를 지금 확인하지 못했어요. 문서 링크를 직접 확인해 주세요."
MISSING_NOTE = "이 설명의 근거를 표시하지 못했어요. 코드와 문서를 직접 확인해 주세요."


class VerificationError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        super().__init__(f"{len(errors)} verification error(s)")
        self.errors = errors

    def report(self) -> str:
        return "\n".join(f"- {error}" for error in self.errors)


def sentence_count(text: str) -> int:
    text = text.strip()
    return len(_SENTENCE_END.findall(text)) or (1 if text else 0)


def _code_identifiers(code: str) -> set[str]:
    return set(_IDENTIFIER.findall(code))


def _symbol_in_code(symbol: str, code: str, identifiers: set[str]) -> bool:
    if symbol.strip() and symbol.strip() in code:
        return True
    names = _IDENTIFIER.findall(symbol)
    return bool(names) and all(name in identifiers for name in names)


def _symbol_hint(lines: list[int], ctx: AnalystContext | CheckedCode) -> str:
    """수정 요청에 붙일 후보: 그 단계 줄에 실제로 있는 호출·정의 이름."""
    wanted = set(lines)
    names = [call.name for call in ctx.facts.calls if wanted & set(call.lines)]
    names += [s.name for s in ctx.facts.symbols if any(s.line <= n <= s.end_line for n in wanted)]
    unique = list(dict.fromkeys(names))[:5]
    return f"(이 줄의 후보: {', '.join(unique)})" if unique else ""


def _check_lines(where: str, lines: list[int], line_count: int, errors: list[str]) -> None:
    if not lines:
        errors.append(f"{where}의 lines가 비어 있습니다.")
    bad = [line for line in lines if not 1 <= line <= line_count]
    if bad:
        shown = ", ".join(str(line) for line in bad)
        errors.append(f"{where}의 {shown}번째 줄은 입력에 없습니다(입력 {line_count}줄).")


def _collect_errors(draft: AnalysisDraft, ctx: AnalystContext, require_counts: bool) -> list[str]:
    errors: list[str] = []
    line_count = ctx.facts.line_count
    check_symbols = ctx.adapter.capabilities.static_verification
    identifiers = _code_identifiers(ctx.code)

    if not draft.steps:
        errors.append("steps가 비어 있습니다. 단계를 하나 이상 만드세요.")
    call_lines = {line for call in ctx.facts.calls for line in call.lines}
    if len(call_lines) >= MIN_CALL_LINES_FOR_SPLIT and len(draft.steps) < 2:
        errors.append(
            f"호출이 {len(call_lines)}개 줄에 걸쳐 있는데 단계가 {len(draft.steps)}개입니다. "
            "한 단계에 한 가지 동작만 담아 2개 이상으로 나누세요."
        )
    if len(draft.steps) > MAX_STEPS:
        errors.append(f"단계가 {len(draft.steps)}개입니다. {MAX_STEPS}개 이하로 묶으세요.")
    step_ids = [step.id for step in draft.steps]
    if len(set(step_ids)) != len(step_ids):
        errors.append(f"단계 id가 중복됩니다: {step_ids}")

    for step in draft.steps:
        where = f"{step.id}단계"
        _check_lines(where, step.lines, line_count, errors)
        if check_symbols and not _symbol_in_code(step.symbol, ctx.code, identifiers):
            errors.append(
                f"{where}의 symbol '{step.symbol}'이 코드에 없습니다. "
                f"코드에 적힌 이름을 쓰세요{_symbol_hint(step.lines, ctx)}."
            )
        for evidence in step.evidence:
            if evidence.badge != "official" and not evidence.note.strip():
                errors.append(f"{where}의 {evidence.badge} 근거에 note가 없습니다.")

    for candidate in draft.decision_candidates:
        _check_lines(f"결정 후보 '{candidate.topic}'", candidate.lines, line_count, errors)
    # 후보를 여럿 적고 결정 지점은 하나만 쓰는 출력을 되돌려 보낸다.
    # (여러 결정을 하나로 합치는 버릇 대응)
    topics = {
        candidate.topic.strip()
        for candidate in draft.decision_candidates
        if candidate.topic.strip()
    }
    expected = min(len(topics), MAX_DECISION_POINTS)
    if require_counts and len(draft.decision_points) < expected:
        errors.append(
            f"결정 후보가 {len(topics)}개인데 결정 지점이 {len(draft.decision_points)}개입니다. "
            f"후보마다 별도 결정 지점을 만들어 {expected}개로 맞추세요"
            "(여러 결정을 하나로 합치지 마세요)."
        )
    dp_ids = [dp.id for dp in draft.decision_points]
    if len(set(dp_ids)) != len(dp_ids):
        errors.append(f"결정 지점 id가 중복됩니다: {dp_ids}")
    for dp in draft.decision_points:
        where = f"결정 지점 {dp.id}"
        if dp.step_id not in step_ids:
            errors.append(f"{where}의 step_id {dp.step_id}는 없는 단계입니다(단계 id: {step_ids}).")
        _check_lines(where, dp.lines, line_count, errors)
        option_ids = sorted(option.id for option in dp.options)
        if option_ids != list(OPTION_IDS):
            errors.append(f"{where}의 선택지는 A, B, C, D 4개여야 합니다(현재 {option_ids}).")
        if dp.code_option not in option_ids:
            errors.append(f"{where}의 code_option {dp.code_option}이 선택지에 없습니다.")
        if not dp.conditions:
            errors.append(f"{where}에 결정을 가른 조건이 없습니다.")
        elif require_counts and len(dp.conditions) < MIN_CONDITIONS:
            errors.append(
                f"{where}의 조건이 {len(dp.conditions)}개입니다. "
                f"서로 다른 관점의 조건을 {MIN_CONDITIONS}개 이상 쓰세요."
            )
        condition_ids = [condition.id for condition in dp.conditions]
        if len(set(condition_ids)) != len(condition_ids):
            errors.append(f"{where}의 조건 id가 중복됩니다: {condition_ids}")
    return errors


def _lookup_names(ctx: AnalystContext) -> dict[str, list]:
    """근거의 symbol 표기(asyncio.gather, gather, 코드의 별칭 호출)를 문서 조회 결과에 잇는다."""
    names: dict[str, list] = {}
    for doc in ctx.docs:
        aliases = {doc.symbol, f"{doc.module}.{doc.symbol}"}
        aliases |= {
            call.name
            for call in ctx.facts.calls
            if call.module == doc.module and call.symbol == doc.symbol
        }
        for alias in aliases:
            names.setdefault(alias, []).append(doc)
    return names


def _matching_docs(symbol: str, step: DraftStep, names: dict[str, list]) -> list:
    """근거 심볼 표기(asyncio.gather, gather, asyncio.gather(...))를 조회 결과에 잇는다.

    괄호 뒤 인자는 떼고, 그래도 없으면 단계의 심볼로 찾는다.
    """
    for candidate in (symbol, symbol.split("(", 1)[0].strip(), step.symbol):
        if candidate in names:
            return names[candidate]
    return []


def _canonical(doc) -> str:
    return doc.symbol if doc.module in {"builtins", "__builtin__"} else f"{doc.module}.{doc.symbol}"


def _apply_badge_policy(
    evidence: DraftEvidence, step: DraftStep, ctx: AnalystContext, names: dict[str, list]
) -> Evidence:
    symbol = evidence.symbol or step.symbol
    note = evidence.note.strip() or None
    if evidence.badge == "official":
        docs = _matching_docs(symbol, step, names)
        exact = next((doc for doc in docs if doc.status == "exact_match"), None)
        if exact is not None and ctx.adapter.capabilities.official_docs:
            # 화면에는 모델의 표기 대신 조회한 정식 이름(asyncio.gather)을 쓴다.
            return Evidence(badge="official", symbol=_canonical(exact), url=exact.url, note=note)
        if any(doc.status == "unavailable" for doc in docs):
            return Evidence(badge="needs_check", symbol=symbol, note=UNAVAILABLE_NOTE)
        return Evidence(badge="inferred", symbol=symbol, note=note or DOWNGRADED_NOTE)
    return Evidence(badge=evidence.badge, symbol=evidence.symbol, note=note)


def verify_analysis(
    draft: AnalysisDraft, ctx: AnalystContext, model: str, require_counts: bool = True
) -> AnalyzeResponse:
    """검증을 통과하면 API 응답으로 옮기고, 실패하면 VerificationError(오류 목록).

    require_counts는 결정 지점 수(후보 대비)와 조건 수 규칙이다. 출력 품질을 끌어올리는
    규칙이라 첫 출력에서만 되돌려 보내고, 고친 출력에서는 모자라도 받는다
    (이것만으로 분석이 실패하지 않게)."""
    if len(draft.decision_points) > MAX_DECISION_POINTS:
        # 넘친 것은 오류로 돌려보내지 않고 앞의 것만 남긴다.
        # (프롬프트가 배울 가치가 큰 순서로 쓰게 한다)
        draft = draft.model_copy(
            update={"decision_points": draft.decision_points[:MAX_DECISION_POINTS]}
        )
    errors = _collect_errors(draft, ctx, require_counts)
    if errors:
        raise VerificationError(errors)

    names = _lookup_names(ctx)
    decision_steps = {dp.step_id for dp in draft.decision_points}
    steps: list[Step] = []
    for step in sorted(draft.steps, key=lambda item: item.id):
        evidence = [_apply_badge_policy(item, step, ctx, names) for item in step.evidence]
        if not evidence:
            evidence = [Evidence(badge="needs_check", symbol=step.symbol, note=MISSING_NOTE)]
        io = (
            StepIO(in_=step.io.in_, out=step.io.out)
            if step.io and step.io.in_.strip() and step.io.out.strip()
            else None
        )
        steps.append(
            Step(
                id=step.id,
                title=step.title,
                symbol=step.symbol,
                lines=sorted(set(step.lines)),
                summary=step.summary,
                io=io,
                evidence=evidence,
                is_decision=step.id in decision_steps,
            )
        )

    decision_points = [
        DecisionPoint(
            id=dp.id,
            step_id=dp.step_id,
            lines=sorted(set(dp.lines)),
            prompt=dp.prompt,
            options=[
                Option(id=option.id, label=option.label, detail=option.detail or None)
                for option in sorted(dp.options, key=lambda option: option.id)
            ],
            code_option=dp.code_option,
            hint=dp.hint,
            conditions=[
                Condition(
                    id=condition.id,
                    label=condition.label,
                    explanation=condition.explanation,
                    splits=sorted(set(condition.splits)),
                )
                for condition in dp.conditions
            ],
        )
        for dp in draft.decision_points
    ]
    return AnalyzeResponse(
        language=ctx.facts.language,
        steps=steps,
        decision_points=decision_points,
        meta=AnalyzeMeta(model=model, tool_calls=ctx.tool_calls()),
    )


# --- 제약(POST /api/constraints) ----------------------------------------


def _constraint_grounding_errors(
    constraint: DraftConstraint,
    checked: CheckedCode,
    code: str,
    identifiers: set[str],
    operational: set[int],
) -> list[str]:
    """코드에 걸 곳이 있는 제약인지 본다."""
    where = f"제약 {constraint.id}"
    errors: list[str] = []
    _check_lines(where, constraint.lines, checked.facts.line_count, errors)
    if not operational & set(constraint.lines):
        errors.append(
            f"{where}의 lines {constraint.lines}가 operational_lines {sorted(operational)}에 "
            "없습니다. 외부 호출·동시성·재시도·자원 관리가 있는 줄에 걸리는 제약만 쓰고, "
            "없으면 빼세요."
        )
    if checked.adapter.capabilities.static_verification and not _symbol_in_code(
        constraint.symbol, code, identifiers
    ):
        errors.append(
            f"{where}의 symbol '{constraint.symbol}'이 코드에 없습니다. 제약이 걸리는 줄의 "
            "이름을 쓰고, 코드에 걸 곳이 없는 제약이면 빼세요"
            f"{_symbol_hint(constraint.lines, checked)}."
        )
    for field in ("assumption", "rationale", "label", "situation"):
        if not getattr(constraint, field).strip():
            errors.append(f"{where}의 {field}이 비어 있습니다.")
    return errors


def _constraint_wording_errors(constraint: DraftConstraint, code: str) -> list[str]:
    """버튼 이름(label)과 상황 설명(situation)이 제 역할만 하는지 본다."""
    where = f"제약 {constraint.id}"
    errors: list[str] = []
    label, situation = constraint.label.strip(), constraint.situation.strip()
    # 그 줄에 이미 적힌 값(timeout=5, Semaphore(5))을 라벨에 쓰면 바뀐 조건이 아니라 코드 설명이다.
    # 코드 전체로 넓히면 retries=3이 있는 코드의 "응답 3초 지연"처럼 단위가 다른 값까지 걸린다.
    code_lines = code.splitlines()
    written = {
        number
        for line in constraint.lines
        if 1 <= line <= len(code_lines)
        for number in _NUMBER.findall(code_lines[line - 1])
    }
    same = [number for number in _NUMBER.findall(label) if number in written]
    if same:
        errors.append(
            f"{where}의 label '{label}'의 {', '.join(same)}은 코드에 이미 적힌 값입니다. "
            "코드 설명이 아니라, 그 값이 버티지 못하게 바뀐 바깥 조건"
            "(예: timeout=1.5면 '응답 3초 지연')을 쓰세요."
        )
    if len(label) > CONSTRAINT_LABEL_MAX_CHARACTERS or _SENTENCE_END.search(label):
        errors.append(
            f"{where}의 label '{label}'은 버튼 이름입니다. 문장부호 없는 "
            f"{CONSTRAINT_LABEL_MAX_CHARACTERS}자 이내 명사구로 쓰세요(현재 {len(label)}자)."
        )
    if "?" in situation or sentence_count(situation) > 1:
        errors.append(
            f"{where}의 situation은 물음표 없는 한 문장이어야 합니다. "
            "질문은 다음 단계에서 따로 만듭니다."
        )
    elif situation.rstrip(".") == label:
        errors.append(f"{where}의 situation이 label과 같습니다. label에 없는 배경·범위를 보태세요.")
    results = [phrase for phrase in _RESULT_PHRASES if phrase in situation]
    if results:
        errors.append(
            f"{where}의 situation에 결과 표현({', '.join(results)})이 있습니다. 바깥 조건이 어떻게 "
            "바뀌었는지까지만 쓰고, 코드에 무슨 일이 생기는지는 사용자가 답하게 두세요."
        )
    if not situation.rstrip(".").endswith("요"):
        errors.append(f"{where}의 situation '{situation}'을 '~해요' 말투로 끝내세요.")
    return errors


def verify_constraints(
    draft: ConstraintsDraft, checked: CheckedCode, code: str, first_try: bool = True
) -> ConstraintsResponse:
    """첫 출력은 근거·문구 오류를 모두 돌려보내 고치게 한다. 고친 출력에서는 오류로 실패시키지
    않고 검사를 모두 통과한 제약만 남긴다. 설계 응용은 선택 단계라, 억지스럽거나 문구가 어긋난
    제약을 보여 주느니 0개로 건너뛰는 편이 낫다."""
    identifiers = _code_identifiers(code)
    operational = checked.adapter.operational_lines(checked.facts)
    if operational is None:  # 정적 분석으로 가를 수 없는 언어는 모델이 고른 줄을 쓴다
        operational = {
            line for line in draft.operational_lines if 1 <= line <= checked.facts.line_count
        }
    if first_try:
        errors: list[str] = []
        ids = [c.id for c in draft.constraints]
        if len(set(ids)) != len(ids):
            errors.append(f"제약 id가 중복됩니다: {ids}")
        labels = [c.label.strip() for c in draft.constraints]
        if len(set(labels)) != len(labels):
            errors.append(f"제약 label이 중복됩니다: {labels}")
        for c in draft.constraints:
            errors += _constraint_grounding_errors(c, checked, code, identifiers, operational)
            errors += _constraint_wording_errors(c, code)
        if errors:
            raise VerificationError(errors)
    kept: list[DraftConstraint] = []
    for c in draft.constraints:
        duplicate = any(k.id == c.id or k.label.strip() == c.label.strip() for k in kept)
        valid = not (
            _constraint_grounding_errors(c, checked, code, identifiers, operational)
            or _constraint_wording_errors(c, code)
        )
        if not duplicate and valid:
            kept.append(c)
    return ConstraintsResponse(
        constraints=[
            Constraint(
                id=c.id.strip(),
                label=c.label.strip(),
                situation=c.situation.strip(),
                lines=sorted(set(c.lines)),
                symbol=c.symbol.strip(),
                assumption=c.assumption.strip(),
                rationale=c.rationale.strip(),
            )
            for c in kept[:MAX_CONSTRAINTS]
        ]
    )
