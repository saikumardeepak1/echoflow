"""Guards against OpenAPI documentation drift (see issue #24 /
docs/PRD.md "API reference"): every route needs a non-empty description and
every request/response schema needs an example that actually matches its own
shape, so `/docs` and `/redoc` stay trustworthy without a human re-checking
them on every change.
"""

import importlib
import pkgutil
from typing import Any

import pytest
from pydantic import BaseModel

from app import schemas
from app.main import app

# Paths that are intentionally excluded from the "every operation needs a
# description" check: FastAPI/Starlette's own auto-generated docs routes,
# which this app does not author descriptions for.
_EXEMPT_PATHS = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}


def _iter_operations(openapi_schema: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    """Yields (path, http_method, operation) for every path operation in the
    schema, skipping the framework-provided docs routes.
    """
    operations = []
    for path, path_item in openapi_schema["paths"].items():
        if path in _EXEMPT_PATHS:
            continue
        for method, operation in path_item.items():
            if method not in {"get", "post", "put", "patch", "delete", "options", "head"}:
                continue
            operations.append((path, method, operation))
    return operations


def _iter_schema_models() -> list[type[BaseModel]]:
    """Every Pydantic model defined in app.schemas.*, discovered by walking
    the package rather than hand-maintaining a list, so a new schema module
    is covered automatically the moment it is added.
    """
    models: list[type[BaseModel]] = []
    for module_info in pkgutil.iter_modules(schemas.__path__, prefix=f"{schemas.__name__}."):
        module = importlib.import_module(module_info.name)
        for attr in vars(module).values():
            if (
                isinstance(attr, type)
                and issubclass(attr, BaseModel)
                and attr.__module__ == module.__name__
            ):
                models.append(attr)
    return models


def test_openapi_schema_is_generatable() -> None:
    """`app.openapi()` must not raise: a route with a broken response_model
    or an unserializable type would blow this up, taking /docs and /redoc
    down with it.
    """
    openapi_schema = app.openapi()

    assert openapi_schema["openapi"]
    assert "paths" in openapi_schema
    assert len(openapi_schema["paths"]) > 0


def test_every_route_has_a_non_empty_description() -> None:
    """Every path operation must expose a non-empty `summary` or
    `description` so Swagger UI / ReDoc render something useful for it,
    rather than a bare method name.
    """
    openapi_schema = app.openapi()
    operations = _iter_operations(openapi_schema)
    assert operations, "expected at least one documented route"

    missing = []
    for path, method, operation in operations:
        description = (operation.get("description") or "").strip()
        summary = (operation.get("summary") or "").strip()
        if not description and not summary:
            missing.append(f"{method.upper()} {path}")

    assert not missing, f"routes missing a description/summary: {missing}"


def test_every_schema_model_has_an_example() -> None:
    """Every request/response schema in app/schemas/*.py must declare at
    least one example via `json_schema_extra`, so the generated OpenAPI
    schema (and therefore Swagger UI / ReDoc) always has sample payloads to
    show, not just field types.
    """
    models = _iter_schema_models()
    assert models, "expected at least one schema model to check"

    missing = []
    for model in models:
        if not _examples_for(model):
            missing.append(model.__name__)

    assert not missing, f"schemas missing a json_schema_extra example: {missing}"


def _examples_for(model: type[BaseModel]) -> list[dict[str, Any]]:
    json_schema_extra = model.model_config.get("json_schema_extra")
    if not isinstance(json_schema_extra, dict):
        return []
    if "examples" in json_schema_extra:
        return list(json_schema_extra["examples"])
    if "example" in json_schema_extra:
        return [json_schema_extra["example"]]
    return []


@pytest.mark.parametrize("model", _iter_schema_models(), ids=lambda model: model.__name__)
def test_documented_examples_validate_against_their_own_schema(model: type[BaseModel]) -> None:
    """Parses every declared example back through its own model. Catches the
    real failure mode of hand-written examples: drifting out of sync with
    the fields/types/constraints the schema actually enforces.
    """
    examples = _examples_for(model)
    if not examples:
        pytest.skip(f"{model.__name__} has no example (covered by the presence test above)")

    for example in examples:
        model.model_validate(example)
