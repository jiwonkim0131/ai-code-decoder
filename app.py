"""FastAPI 진입점. Vercel이 이 파일의 `app`을 찾아 함수 하나로 배포한다."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

# 로컬 개발용 .env를 서버 모듈보다 먼저 읽는다. 배포 환경에는 .env가 없고 환경변수만 있다.
load_dotenv(Path(__file__).with_name(".env"), override=False)

from fastapi import FastAPI  # noqa: E402

from server.agents.runtime import configure_agents  # noqa: E402
from server.config import get_settings  # noqa: E402
from server.errors import install_error_handlers  # noqa: E402
from server.frontend import install_frontend  # noqa: E402
from server.limits import install_body_limit  # noqa: E402
from server.logs import install_request_logging  # noqa: E402
from server.routes import router as api_router  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_agents(settings)

    mcp = None
    if settings.mcp_enabled:
        from server.mcp_server import create_mcp_server
        from server.sealing import Sealer

        mcp = create_mcp_server(Sealer(settings.mcp_seal_key or ""))

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if mcp is None:
            yield
            return
        # MCP 세션 관리자는 앱이 떠 있는 동안 작업 그룹을 돌려야 요청을 받는다.
        async with mcp.session_manager.run():
            yield

    app = FastAPI(title="AI Code Tutor API", version="0.1.0", lifespan=lifespan)
    install_error_handlers(app)
    install_body_limit(app)
    install_request_logging(app)

    @app.get("/api/health", tags=["system"])
    def health() -> dict[str, bool]:
        return {"ok": True}

    app.include_router(api_router)

    if settings.enable_dev_routes:
        from server.dev import router as dev_router

        app.include_router(dev_router)

    if mcp is not None:
        from server.mcp_server import mcp_routes

        # streamable_http_app()이 세션 관리자를 만들므로 lifespan이 돌기 전에 붙인다.
        app.router.routes.extend(mcp_routes(mcp, settings.mcp_tokens))

    install_frontend(app)  # 가장 마지막: /api 라우트가 먼저 매칭된다
    return app


app = create_app()
