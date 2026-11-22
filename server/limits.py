"""요청 본문 크기 상한(BE-09). 코드 20,000자와 FE가 다시 보내는 값이 들어가고도 남는 크기다."""

from __future__ import annotations

from fastapi import FastAPI, Request

from server.errors import ErrorCode, error_response

MAX_BODY_BYTES = 256 * 1024


def install_body_limit(app: FastAPI) -> None:
    @app.middleware("http")
    async def limit_body(request: Request, call_next):
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > MAX_BODY_BYTES:
            return error_response(413, ErrorCode.INPUT_INVALID, "요청이 너무 커요.")
        return await call_next(request)
