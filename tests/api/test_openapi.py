"""Tests that the generated OpenAPI document fully describes every string endpoint."""

from fastapi.routing import APIRoute

from src.api.routes.strings import router as strings_router


def _strings_post_paths() -> set[str]:
    return {
        route.path
        for route in strings_router.routes
        if isinstance(route, APIRoute) and "POST" in route.methods
    }


def _schema_ref(schema: dict) -> str:
    assert "$ref" in schema, f"expected a $ref to a named component schema, got {schema!r}"
    ref = schema["$ref"]
    assert ref.startswith("#/components/schemas/"), f"unexpected $ref target: {ref!r}"
    return ref.removeprefix("#/components/schemas/")


def test_openapi_json_returns_200(client):
    response = client.get("/openapi.json")

    assert response.status_code == 200


def test_openapi_document_has_title_and_version(client):
    response = client.get("/openapi.json")
    spec = response.json()

    assert spec["info"]["title"]
    assert spec["info"]["version"]


def test_every_strings_post_route_has_named_request_and_response_schemas(client):
    response = client.get("/openapi.json")
    spec = response.json()

    expected_paths = _strings_post_paths()
    assert expected_paths, "expected the strings router to register at least one POST route"

    component_schemas = spec["components"]["schemas"]

    for path in expected_paths:
        assert path in spec["paths"], f"{path} is missing from the OpenAPI paths object"
        post = spec["paths"][path].get("post")
        assert post is not None, f"{path} has no POST operation in the OpenAPI document"

        request_schema = post["requestBody"]["content"]["application/json"]["schema"]
        request_schema_name = _schema_ref(request_schema)
        assert request_schema_name in component_schemas

        assert "200" in post["responses"], (
            f"{path} has no 200 response documented in the OpenAPI document"
        )
        response_schema = post["responses"]["200"]["content"]["application/json"]["schema"]
        response_schema_name = _schema_ref(response_schema)
        assert response_schema_name in component_schemas
