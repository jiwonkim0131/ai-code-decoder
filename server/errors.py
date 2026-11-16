"""오류 응답 형식 `{ "error": { "code", "message", "details"? } }` 하나로 통일한다(BE-02)."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("server.errors")


class ErrorCode:
    INPUT_INVALID = "input_invalid"
    LANGUAGE_UNSUPPORTED = "language_unsupported"
    SYNTAX_ERROR = "syntax_error"
    SECRETS_DETECTED = "secrets_detected"
    LLM_UNAVAILABLE = "llm_unavailable"
    ANALYSIS_FAILED = "analysis_failed"
    RATE_LIMITED = "rate_limited"
    NOT_FOUND = "not_found"
    NOT_IMPLEMENTED = "not_implemented"
    INTERNAL = "internal_error"


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details


def error_response(status: int, code: str, message: str, details: dict | None = None):
    error: dict[str, Any] = {"code": code, "message": message}
    if details:
        error["details"] = details  # findings, line 등은 error 안에만 둔다
    return JSONResponse({"error": error}, status_code=status)


def _validation_fields(exc: RequestValidationError) -> list[dict[str, str]]:
    # 입력값(코드 원문일 수 있음)은 응답에 되돌려 보내지 않고 위치와 사유만 준다.
    return [
        {
            "field": ".".join(str(part) for part in err.get("loc", ()) if part != "body"),
            "reason": err.get("type", "invalid"),
        }
        for err in exc.errors()
    ]


def _openapi_without_422(app: FastAPI):
    """검증 실패는 400 input_invalid로 나가므로 FastAPI가 자동으로 넣는 422 응답을 계약에서 뺀다."""

    def openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
        for operations in schema.get("paths", {}).values():
            for operation in operations.values():
                operation.get("responses", {}).pop("422", None)
        for name in ("HTTPValidationError", "ValidationError"):
            schema.get("components", {}).get("schemas", {}).pop(name, None)
        app.openapi_schema = schema
        return schema

    return openapi


def install_error_handlers(app: FastAPI) -> None:
    app.openapi = _openapi_without_422(app)

    @app.exception_handler(ApiError)
    async def on_api_error(_: Request, exc: ApiError):
        return error_response(exc.status, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def on_invalid(_: Request, exc: RequestValidationError):
        # FastAPI 기본값은 422 + detail 모양이라 400 input_invalid로 맞춘다.
        return error_response(
            400,
            ErrorCode.INPUT_INVALID,
            "입력을 확인해 주세요.",
            {"fields": _validation_fields(exc)},
        )

    @app.exception_handler(StarletteHTTPException)
    async def on_http_error(_: Request, exc: StarletteHTTPException):
        code = ErrorCode.NOT_FOUND if exc.status_code == 404 else f"http_{exc.status_code}"
        return error_response(exc.status_code, code, str(exc.detail))

    @app.exception_handler(Exception)
    async def on_unexpected(_: Request, exc: Exception):
        # 예외 메시지에 코드 조각이 섞일 수 있어 종류만 남긴다.
        logger.error("unhandled_error type=%s", type(exc).__name__)
        return error_response(500, ErrorCode.INTERNAL, "서버 오류가 발생했어요.")
