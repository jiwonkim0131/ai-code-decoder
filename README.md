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

## 환경변수

| 이름 | 설명 |
| --- | --- |
| `OPENAI_API_KEY` | OpenAI API 키. 로컬은 `.env`, 배포는 Vercel 환경변수에만 둔다 |
| `OPENAI_MODEL` | 에이전트가 쓰는 모델 이름 |
| `ALLOWED_LANGS` | 분석 허용 언어(쉼표 구분). 기본 `python` |
| `ENABLE_DEV_ROUTES` | `true`면 에이전트 시험 API(`POST /api/dev/agent-ping`)를 연다. 배포에서는 `false` |

## 배포 (Vercel)

Vercel이 루트의 `app.py`에서 FastAPI `app`을 찾는다. `vercel link` → `vercel dev`로 로컬 확인 후, 대시보드에 환경변수를 등록하고 배포한다.
