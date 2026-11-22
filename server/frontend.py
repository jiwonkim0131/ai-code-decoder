"""빌드한 FE(web/dist)를 같은 도메인에서 서빙한다(설계 문서 app.frontend). 빌드가 없으면 건너뛴다.

/api/*는 서빙하지 않으므로 없는 API 경로는 그대로 404 오류 형식이 나간다.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse

DIST = Path(__file__).resolve().parents[1] / "web" / "dist"
NO_CACHE = "no-cache"
IMMUTABLE = "public, max-age=31536000, immutable"


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
            # 빌드 산출물(assets/)은 파일 이름에 해시가 있어 내용이 바뀌지 않는다.
            cache = IMMUTABLE if path.startswith("/assets/") else NO_CACHE
            return FileResponse(file, headers={"Cache-Control": cache})
        if path.startswith("/assets/"):
            # 이전 배포의 index.html이 지난 해시의 JS를 찾으면 HTML이 아니라 404를 준다
            # (HTML을 주면 브라우저가 모듈 MIME 오류를 내며 흰 화면이 된다).
            return response
        # 클라이언트 라우팅은 index.html이 맡는다. 배포마다 새 해시를 가리키니 매번 다시 확인한다.
        return FileResponse(index, headers={"Cache-Control": NO_CACHE})
