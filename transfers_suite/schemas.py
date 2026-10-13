"""Validates responses against the schemas in spec/transfers_openapi.json (OpenAPI 3.1 = JSON Schema 2020-12)."""

import json
from functools import cache
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

SPEC_PATH = Path(__file__).parent / "spec" / "transfers_openapi.json"
SPEC_URI = "urn:cdp:transfers-openapi"


@cache
def _registry() -> Registry:
    spec = json.loads(SPEC_PATH.read_text())
    return Registry().with_resource(SPEC_URI, Resource(contents=spec, specification=DRAFT202012))


@cache
def _validator(schema_name: str) -> Draft202012Validator:
    schema = {"$ref": f"{SPEC_URI}#/components/schemas/{schema_name}"}
    return Draft202012Validator(schema, registry=_registry())


def schema_errors(instance, schema_name: str) -> list[str]:
    errors = sorted(_validator(schema_name).iter_errors(instance), key=lambda e: list(e.absolute_path))
    return [f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}" for e in errors]


def assert_schema(instance, schema_name: str) -> None:
    errors = schema_errors(instance, schema_name)
    assert not errors, f"Response does not match {schema_name} schema:\n  " + "\n  ".join(errors[:20])


def list_response_errors(instance) -> list[str]:
    """ListTransfers is an inline schema in the spec; check its shape explicitly."""
    if not isinstance(instance, dict) or not isinstance(instance.get("transfers"), list):
        return ["<root>: expected object with a 'transfers' array"]
    errors = []
    token = instance.get("nextPageToken")
    if token is not None and not isinstance(token, str):
        errors.append("nextPageToken: expected string")
    for i, item in enumerate(instance["transfers"]):
        errors += [f"transfers/{i}/{e}" for e in schema_errors(item, "Transfer")]
    return errors
