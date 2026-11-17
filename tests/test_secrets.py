from __future__ import annotations

import ast

import pytest

from server.secrets import REDACTED, redact, scan_secrets

# 테스트용 가짜 값. pre-commit 훅의 OpenAI 키 패턴에 걸리지 않도록 조합해서 만든다.
OPENAI_KEY = "sk-" + "proj-" + "Ab3dEf6hIj9lMn2pQr5tUv8xYz1bCd4fGh7"
GITHUB_TOKEN = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
AWS_KEY = "AKIA" + "IOSFODNN7EXAMPL1"
RANDOM_TOKEN = "a8Kq3ZpL0vN7xR2tY9wB4mC6eF1hJ5sD"


def _kinds(code: str) -> list[str]:
    return [f.kind for f in scan_secrets(code)]


@pytest.mark.parametrize(
    "line,kind",
    [
        (f'client = OpenAI(api_key="{OPENAI_KEY}")', "openai_api_key"),
        (f"# key: {OPENAI_KEY}", "openai_api_key"),
        (f'TOKEN = "{GITHUB_TOKEN}"', "github_token"),
        (f'aws = "{AWS_KEY}"', "aws_access_key"),
        ('DATABASE_URL = "postgres://admin:s3cr3tPw@db:5432/app"', "url_password"),
        ('db_password: str = "hunter2Pass99"', "assigned_secret"),
        ('config = {"client_secret": "q8Zr2Lm0Vx"}', "assigned_secret"),
        (f'blob = "{RANDOM_TOKEN}"', "high_entropy_string"),
    ],
)
def test_detects_secret_kinds(line, kind):
    findings = scan_secrets(f"import os\n{line}\n")
    assert [f.kind for f in findings] == [kind]
    finding = findings[0]
    assert finding.line == 2
    secret = line[finding.start : finding.end]
    assert finding.preview.startswith(secret[:4])
    assert secret not in finding.preview


@pytest.mark.parametrize(
    "line",
    [
        'api_key = os.getenv("OPENAI_API_KEY")',
        'tokenizer = "bert-base-uncased"',
        'password_prompt = "Enter your password:"',
        'TOKEN_URL = "https://example.com/oauth/token"',
        'API_KEY_ENV = "OPENAI_API_KEY"',
        'api_key = "your-api-key"',
        'if password == "correcthorse":',
        'digest = "d41d8cd98f00b204e9800998ecf8427e"',
        "results = await asyncio.gather(*tasks, return_exceptions=True)",
        f'api_key = "{REDACTED}"',
    ],
)
def test_ignores_ordinary_code_and_placeholders(line):
    assert scan_secrets(line) == []


def test_redacted_code_has_no_findings_and_still_parses():
    code = (
        "import os\n"
        f'OPENAI_API_KEY = "{OPENAI_KEY}"\n'
        f'headers = {{"Authorization": "token {GITHUB_TOKEN}", "x": "{RANDOM_TOKEN}"}}\n'
        'url = "postgres://admin:s3cr3tPw@db:5432/app"\n'
    )
    findings = scan_secrets(code)
    assert len(findings) == 4

    redacted = redact(code, findings)

    assert scan_secrets(redacted) == []
    assert OPENAI_KEY not in redacted and GITHUB_TOKEN not in redacted
    assert redacted.count(REDACTED) == 4
    ast.parse(redacted)


def test_private_key_block_is_flagged_line_by_line_and_keeps_code_around_it():
    code = (
        'KEY = """-----BEGIN RSA PRIVATE KEY-----\n'
        "MIIEowIBAAKCAQEA1234567890abcdef\n"
        '-----END RSA PRIVATE KEY-----"""\n'
        "print(KEY)\n"
    )
    findings = scan_secrets(code)
    assert [(f.line, f.kind) for f in findings] == [
        (1, "private_key"),
        (2, "private_key"),
        (3, "private_key"),
    ]

    redacted = redact(code, findings)

    assert redacted.startswith(f'KEY = """{REDACTED}\n')
    assert scan_secrets(redacted) == []
    ast.parse(redacted)


def test_crlf_line_endings_keep_offsets():
    code = f'x = 1\r\napi = "{OPENAI_KEY}"\r\n'
    [finding] = scan_secrets(code)
    line = code.split("\n")[1]
    assert line[finding.start : finding.end] == OPENAI_KEY


def test_long_line_scans_quickly():
    # 정규식 역추적으로 느려지지 않는지(입력 상한 20,000자)
    assert scan_secrets("a" * 19_999) == []
    assert scan_secrets("x = " + "token" * 3_990) == []
