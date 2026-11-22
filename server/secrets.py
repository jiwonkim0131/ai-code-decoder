"""비밀값 스캔(BE-04). 키 패턴 정규식과 엔트로피로 후보를 찾는다.

`POST /api/scan`과 코드를 받는 LLM 엔드포인트의 입구가 같은 순수 함수 `scan_secrets`를 쓴다.
FE는 finding의 (line, start, end) 범위를 `REDACTED`로 치환하며, 이 치환값은 다시 후보로
잡히지 않는다. start·end는 그 줄 안의 문자(코드 포인트) 위치이고 end는 미포함이다.
줄은 "\\n" 기준으로 나눈다(FE와 같은 기준).
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from server.models import SecretFinding

REDACTED = "<REDACTED>"

_PREVIEW_PREFIX = 4
_MIN_ASSIGNED_SECRET = 8
_MIN_ENTROPY_STRING = 32
_ASSIGNED_ENTROPY = 3.0
_STRING_ENTROPY = 4.2


@dataclass(frozen=True)
class _Pattern:
    kind: str
    regex: re.Pattern[str]
    group: int = 0  # 이 그룹의 범위만 후보로 잡는다


# 앞에 있는 패턴이 우선한다(같은 범위가 겹치면 앞의 kind를 쓴다).
# 한 줄이 20,000자까지 올 수 있어 반복 길이를 묶어 역추적이 커지지 않게 한다.
_PATTERNS: tuple[_Pattern, ...] = (
    _Pattern("anthropic_api_key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}")),
    _Pattern("openai_api_key", re.compile(r"\bsk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_-]{20,}")),
    _Pattern("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    _Pattern("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}|\bgithub_pat_\w{22,}")),
    _Pattern("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}")),
    _Pattern("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    _Pattern("stripe_key", re.compile(r"\b[rs]k_(?:live|test)_[A-Za-z0-9]{16,}")),
    _Pattern("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    # scheme://user:password@host 의 password 부분
    _Pattern(
        "url_password",
        re.compile(r"\b[A-Za-z][A-Za-z0-9+.-]{0,30}://[^\s:/@'\"]{1,256}:([^\s@'\"]{1,256})@"),
        1,
    ),
    # api_key = "...", "token": "...", password="..." 처럼 이름이 비밀값을 가리키는 문자열.
    # 이름은 키워드로 끝나야 한다(tokenizer, token_count 같은 이름은 제외).
    _Pattern(
        "assigned_secret",
        re.compile(
            r"(?i)\b\w{0,64}?(?:api_?key|secret|tokens?|passw(?:or)?d|pwd|credentials?|access_?key|"
            r"private_?key)(?:_?(?:key|value))?\b['\"]?\s*(?::\s*[A-Za-z_][\w\[\]., |]*?\s*)?"
            r"[:=](?!=)\s*[rRbBuU]{0,2}(['\"])([^'\"\n]*)\1"
        ),
        2,
    ),
)

_PRIVATE_KEY_BEGIN = re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----")
_PRIVATE_KEY_END = re.compile(r"-----END (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----")
_QUOTED_TOKEN = re.compile(rf"(['\"])([A-Za-z0-9+/=_-]{{{_MIN_ENTROPY_STRING},}})\1")
_PLACEHOLDER = re.compile(
    r"(?i)redacted|^\*+$|^x+$|^\.+$|^<.*>$|^\$\{.*\}$|^\{\{.*\}\}$|your[_-]|changeme|"
    r"example|placeholder|dummy"
)
_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")


def _entropy(value: str) -> float:
    counts = Counter(value)
    total = len(value)
    return -sum(n / total * math.log2(n / total) for n in counts.values())


def _is_placeholder(value: str) -> bool:
    return bool(_PLACEHOLDER.search(value)) or len(set(value)) <= 2


def _is_assigned_secret(value: str) -> bool:
    """이름이 비밀값을 가리키는 문자열 중 실제 값처럼 보이는 것만 고른다."""
    if len(value) < _MIN_ASSIGNED_SECRET or any(ch.isspace() for ch in value):
        return False
    if "://" in value or _ENV_NAME.match(value):  # URL, 환경변수 이름
        return False
    return _entropy(value) >= _ASSIGNED_ENTROPY


def _is_random_token(value: str) -> bool:
    has_letter = any(ch.isalpha() for ch in value)
    has_digit = any(ch.isdigit() for ch in value)
    return has_letter and has_digit and _entropy(value) >= _STRING_ENTROPY


def _preview(value: str) -> str:
    return f"{value[:_PREVIEW_PREFIX]}…({len(value)}자)"


def _line_findings(line_no: int, line: str) -> list[SecretFinding]:
    spans: list[tuple[int, int, str]] = []

    def add(start: int, end: int, kind: str) -> None:
        if end <= start or any(start < e and s < end for s, e, _ in spans):
            return
        if _is_placeholder(line[start:end]):
            return
        spans.append((start, end, kind))

    for pattern in _PATTERNS:
        for match in pattern.regex.finditer(line):
            start, end = match.span(pattern.group)
            if pattern.kind == "assigned_secret" and not _is_assigned_secret(line[start:end]):
                continue
            add(start, end, pattern.kind)
    for match in _QUOTED_TOKEN.finditer(line):
        if _is_random_token(match.group(2)):
            add(*match.span(2), "high_entropy_string")

    return [
        SecretFinding(
            line=line_no, start=start, end=end, kind=kind, preview=_preview(line[start:end])
        )
        for start, end, kind in sorted(spans)
    ]


def _private_key_spans(lines: list[str]) -> dict[int, tuple[int, int]]:
    """PEM 개인 키 블록은 본문이 여러 줄이라 BEGIN부터 END까지 줄마다 범위를 잡는다.

    BEGIN 줄은 BEGIN부터 줄 끝까지, END 줄은 줄 첫 글자부터 END까지만 잡아 `KEY = \"\"\"`
    같은 바깥 코드는 남긴다. END가 없으면 BEGIN 줄만 잡는다.
    """
    spans: dict[int, tuple[int, int]] = {}
    index = 0
    while index < len(lines):
        begin = _PRIVATE_KEY_BEGIN.search(lines[index])
        if not begin:
            index += 1
            continue
        end_index, end = index, _PRIVATE_KEY_END.search(lines[index], begin.end())
        while end is None and end_index + 1 < len(lines):
            end_index += 1
            end = _PRIVATE_KEY_END.search(lines[end_index])
        if end is None:
            spans[index] = (begin.start(), len(lines[index]))
            index += 1
            continue
        if end_index == index:
            spans[index] = (begin.start(), end.end())
        else:
            spans[index] = (begin.start(), len(lines[index]))
            for middle in range(index + 1, end_index):
                body = lines[middle].strip()
                if body:
                    start = lines[middle].index(body)
                    spans[middle] = (start, start + len(body))
            first = len(lines[end_index]) - len(lines[end_index].lstrip())
            spans[end_index] = (first, end.end())
        index = end_index + 1
    return spans


def scan_secrets(code: str) -> list[SecretFinding]:
    """코드에서 비밀값 후보를 줄 순서대로 돌려준다. 코드는 실행하지 않고 로그에 남기지 않는다."""
    lines = [line.removesuffix("\r") for line in code.split("\n")]
    key_spans = _private_key_spans(lines)
    findings: list[SecretFinding] = []
    for index, line in enumerate(lines):
        if index in key_spans:
            start, end = key_spans[index]
            findings.append(
                SecretFinding(
                    line=index + 1,
                    start=start,
                    end=end,
                    kind="private_key",
                    preview=_preview(line[start:end]),
                )
            )
            continue
        findings.extend(_line_findings(index + 1, line))
    return findings


def redact(code: str, findings: list[SecretFinding]) -> str:
    """findings 범위를 REDACTED로 바꾼다. FE의 "값 가리기"와 같은 규칙(테스트·도구용)."""
    lines = code.split("\n")
    by_line: dict[int, list[SecretFinding]] = {}
    for finding in findings:
        by_line.setdefault(finding.line, []).append(finding)
    for line_no, items in by_line.items():
        line = lines[line_no - 1]
        for finding in sorted(items, key=lambda f: f.start, reverse=True):
            line = line[: finding.start] + REDACTED + line[finding.end :]
        lines[line_no - 1] = line
    return "\n".join(lines)
