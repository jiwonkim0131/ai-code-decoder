# AI Code Decoder

AI가 짜 준 Python 코드를 붙여넣으면 흐름을 단계로 설명하고, 설계 결정 지점에서 사용자가 먼저 판단해 보게 하는 학습 웹앱이다. FastAPI 서버 하나가 OpenAI Agents SDK 에이전트(분석·판정·코치)를 JSON API로 내놓고, React SPA가 5단계 학습 화면을 그린다. 서버는 아무것도 저장하지 않는다.

## 요구 사항

- Python 3.12, [uv](https://docs.astral.sh/uv/)
- OpenAI API 키

## 시작하기

```bash
git config core.hooksPath .githooks   # 비밀값 커밋 차단 훅 (클론 후 1회)
cp .env.example .env                   # 값을 채운다. .env는 커밋되지 않는다
uv sync                                # .venv 생성과 의존성 설치
uv run uvicorn app:app --reload        # http://127.0.0.1:8000/api/health, /docs
```

## 자주 쓰는 명령

| 명령 | 용도 |
| --- | --- |
| `uv run pytest` | 테스트 (OpenAI 호출 없음) |
| `uv run ruff check . && uv run ruff format --check .` | 린트·포맷 확인 |
| `uv run python scripts/export_openapi.py` | `contract/openapi.json` 갱신 (FE 타입 생성용) |
| `uv run python -m evals.golden [--case ID] [--save]` | 골든 샘플 평가. **실제 OpenAI를 부른다**(비용 발생). `--save`는 응답을 `evals/golden/out/`에 저장 |

## 환경변수

| 이름 | 설명 |
| --- | --- |
| `OPENAI_API_KEY` | OpenAI API 키. 로컬은 `.env`, 배포는 Vercel 환경변수에만 둔다 |
| `OPENAI_MODEL` | 에이전트가 쓰는 모델 이름 |
| `ALLOWED_LANGS` | 분석 허용 언어(쉼표 구분). 기본 `python` |
| `AGENT_TIMEOUT_SECONDS` | 요청 하나에서 에이전트 실행(수정 요청 포함)에 쓰는 최대 시간. 기본 `60`. 넘으면 502 `llm_unavailable` |
| `ENABLE_DEV_ROUTES` | `true`면 실측용 API(`POST /api/dev/agent-ping`, `POST /api/dev/lookup-docs`)를 연다. 배포에서는 `false` |

## 배포 (Vercel)

Vercel이 루트의 `app.py`에서 FastAPI `app`을 찾는다. `vercel link` → `vercel dev`로 로컬 확인 후, 대시보드에 환경변수를 등록하고 배포한다.

## 비용·남용 보호 (BE-09)

코드로 막는 것: 입력 20,000자, 요청 본문 256KB, FE가 다시 보내는 목록 상한(steps 8개, evidence 20개),
에이전트 최대 턴(분석 6, 판정·코치 1)과 문서 조회 5회, 출력 토큰 상한(분석 6,000 · 판정 2,000 · 코치 1,000),
요청당 시간 상한(`AGENT_TIMEOUT_SECONDS`). 요청마다 `kind=agent:*` 로그 한 줄에 토큰 수·도구 호출 수·실패 코드를 남긴다.

배포 전에 손으로 해야 하는 것:

- OpenAI 대시보드(Settings → Limits)에서 프로젝트 월 사용 한도를 건다. 한도에 닿으면 API는 502 `llm_unavailable`을 돌려준다.
- Vercel 대시보드(Firewall)에서 `/api/analyze` 등에 IP 기준 rate limit 규칙을 걸 수 있는지 Hobby 플랜에서 확인한다.
  안 되면 위의 상한만으로 막는다. 사용자별 한도는 저장소가 필요해 만들지 않았다.

| 상황 | 응답 |
| --- | --- |
| OpenAI 호출 한도(429) | 429 `rate_limited` |
| OpenAI 월 한도 소진, 장애, 인증 실패, 시간 초과 | 502 `llm_unavailable` |
| `OPENAI_API_KEY` 없음 | 503 `llm_unavailable` |
| 검증 2회 실패, 도구 계약 위반, 최대 턴 초과 | 502 `analysis_failed` |

## 골든 샘플 평가 (BE-10)

`evals/golden/cases.jsonl` 한 줄이 케이스 하나다. 분석 대상 코드는 `evals/golden/samples/`에 있고,
평가는 FE와 같은 순서(analyze → judge → scenario → feedback)로 API를 불러 `evals/golden/checks.py`의 규칙으로 본다.

| 케이스 | 보는 것 |
| --- | --- |
| `agent-tools` | 목업 사례. 9번째 줄 asyncio.gather 결정 지점, gather 공식 문서 배지, 판정 방향(조건을 짚은 답 / 이유 없는 답 / 건너뛰기), "tool 하나 3초 지연" |
| `fetch-users` | 서드파티 패키지(requests) 공식 문서 배지, 재시도 결정 지점, "rate limit 분당 60회" |
| `prompt-injection` | 주석 속 가짜 SYSTEM 지시에 넘어가지 않는지(단계 수, 줄 번호, 배지) |

프롬프트를 고칠 때마다 돌려 회귀를 확인한다. 검사 규칙 자체의 테스트(`tests/test_golden.py`)는 OpenAI 없이 돈다.
