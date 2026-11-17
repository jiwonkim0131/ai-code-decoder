"""빌드한 FE(web/dist)를 같은 도메인에서 서빙한다(설계 문서 app.frontend). 빌드가 없으면 건너뛴다.

/api/*는 서빙하지 않으므로 없는 API 경로는 그대로 404 오류 형식이 나간다.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse

DIST = Path(__file__).resolve().parents[1] / "web" / "dist"


def install_frontend(app: FastAPI, dist: Path = DIST) -> None:
    index = dist / "index.html"
    if not index.is_file():
        return
    root = dist.resolve()

    # 라우트로 잡으면 /api의 없는 경로가 405가 되므로, 다른 라우트가 404를 낸 GET만 FE로 돌린다.
    @app.middleware("http")
    async def serve_frontend(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if response.status_code != 404 or request.method != "GET" or path.startswith("/api"):
            return response
        file = (root / path.lstrip("/")).resolve()
        if file.is_file() and root in file.parents:
            return FileResponse(file)
        return FileResponse(index)  # 클라이언트 라우팅은 index.html이 맡는다
