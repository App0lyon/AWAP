from pathlib import Path

from fastapi.testclient import TestClient

from awap.api.app import create_app


def _database_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'awap-frontend.db'}"


def test_frontend_editor_is_served(tmp_path: Path) -> None:
    client = TestClient(create_app(database_url=_database_url(tmp_path)))

    response = client.get("/")

    assert response.status_code == 200
    assert "Workflow Editor" in response.text
    assert "/static/app.js" in response.text


def test_frontend_static_assets_are_served(tmp_path: Path) -> None:
    client = TestClient(create_app(database_url=_database_url(tmp_path)))

    css_response = client.get("/static/styles.css")
    js_response = client.get("/static/app.js")

    assert css_response.status_code == 200
    assert "workspace-grid" in css_response.text
    assert js_response.status_code == 200
    assert "saveNewWorkflow" in js_response.text
