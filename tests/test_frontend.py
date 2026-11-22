from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.errors import install_error_handlers
from server.frontend import install_frontend


def _client(tmp_path) -> TestClient:
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<html>app</html>")
    (tmp_path / "assets" / "app.js").write_text("console.log(1)")
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/api/health")
    def health():
        return {"ok": True}

    install_frontend(app, tmp_path)
    return TestClient(app)


def test_serves_index_assets_and_spa_fallback(tmp_path):
    client = _client(tmp_path)
    assert client.get("/").text == "<html>app</html>"
    assert client.get("/flow").text == "<html>app</html>"
    assert client.get("/assets/app.js").text == "console.log(1)"
    assert client.get("/api/health").json() == {"ok": True}


def test_unknown_api_path_is_not_swallowed_by_spa(tmp_path):
    response = _client(tmp_path).get("/api/nope")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_path_traversal_falls_back_to_index(tmp_path):
    (tmp_path.parent / "secret.txt").write_text("secret")
    assert "secret" not in _client(tmp_path).get("/..%2Fsecret.txt").text


def test_no_dist_means_no_frontend(tmp_path):
    app = FastAPI()
    install_frontend(app, tmp_path / "missing")
    assert TestClient(app).get("/").status_code == 404


def test_unknown_api_post_stays_404(tmp_path):
    response = _client(tmp_path).post("/api/dev/agent-ping")
    assert response.status_code == 404


def test_index_is_revalidated_and_assets_are_immutable(tmp_path):
    client = _client(tmp_path)
    assert client.get("/").headers["cache-control"] == "no-cache"
    assert client.get("/flow").headers["cache-control"] == "no-cache"
    assert "immutable" in client.get("/assets/app.js").headers["cache-control"]


def test_missing_asset_is_404_not_index(tmp_path):
    # 지난 배포의 해시 JS를 HTML로 주면 브라우저가 모듈 MIME 오류로 흰 화면이 된다.
    response = _client(tmp_path).get("/assets/index-OLDHASH.js")
    assert response.status_code == 404
    assert "<html>" not in response.text
