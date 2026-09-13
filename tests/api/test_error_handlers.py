from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.api.app import app as real_app
from src.api.app import http_exception_handler, validation_exception_handler


class Item(BaseModel):
    name: str
    quantity: int


def build_test_app() -> FastAPI:
    test_app = FastAPI()
    test_app.add_exception_handler(RequestValidationError, validation_exception_handler)
    test_app.add_exception_handler(StarletteHTTPException, http_exception_handler)

    @test_app.post("/items")
    def create_item(item: Item):
        return item

    return test_app


client = TestClient(build_test_app())
real_client = TestClient(real_app)


def test_missing_required_field_returns_422_with_field_name():
    response = client.post("/items", json={"quantity": 1})

    assert response.status_code == 422
    body = response.json()
    field_names = [error["loc"][-1] for error in body["detail"]]
    assert "name" in field_names


def test_wrong_typed_field_returns_422_with_field_name():
    response = client.post("/items", json={"name": "widget", "quantity": "not-a-number"})

    assert response.status_code == 422
    body = response.json()
    field_names = [error["loc"][-1] for error in body["detail"]]
    assert "quantity" in field_names


def test_unknown_path_returns_json_404():
    response = real_client.get("/this-path-does-not-exist")

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {"detail": "Not Found"}
