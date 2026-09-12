from fastapi import FastAPI


def test_app_imports_as_fastapi_instance():
    from src.api.app import app

    assert isinstance(app, FastAPI)


def test_unknown_path_returns_json_404(client):
    response = client.get("/this-path-does-not-exist")

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {"detail": "Not Found"}
