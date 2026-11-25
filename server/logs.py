"""로그 규칙(BE-02): 코드 원문과 요청 본문은 로그에 남기지 않는다.

요청마다 한 줄만 남기며, 허용된 필드(요청 종류, 상태, 소요 시간, 입력 줄 수, 토큰 수,
도구 호출 횟수, 검증 실패 코드)만 쓸 수 있다.
"""

from __future__ import annotations

import logging
import time

from fastapi import FastAPI, Request

logger = logging.getLogger("server.request")

ALLOWED_FIELDS = frozenset(
    {"kind", "status", "duration_ms", "lines", "tokens", "tool_calls", "failure"}
)


def format_log_fields(**fields: object) -> str:
    unknown = set(fields) - ALLOWED_FIELDS
    if unknown:
        raise ValueError(f"로그에 허용되지 않은 필드: {sorted(unknown)}")
    return " ".join(f"{key}={value}" for key, value in fields.items() if value is not None)


def log_event(**fields: object) -> None:
    logger.info(format_log_fields(**fields))


def install_request_logging(app: FastAPI) -> None:
    @app.middleware("http")
    async def log_request(request: Request, call_next):
        started = time.perf_counter()
        status = 500  # 처리되지 않은 예외도 한 줄 남긴다(500 응답은 바깥 미들웨어가 만든다)
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            # /mcp는 토큰 없는 시도(401)도 세도록 HTTP 요청 단위로 남긴다(도구별 줄은 mcp_server).
            if request.url.path.startswith("/api/") or request.url.path == "/mcp":
                log_event(
                    kind=f"{request.method} {request.url.path}",
                    status=status,
                    duration_ms=int((time.perf_counter() - started) * 1000),
                )
