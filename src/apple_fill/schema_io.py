"""Load JSON schemas and turn schema errors into short messages."""

from __future__ import annotations

import json
from functools import lru_cache
from importlib.resources import files

from jsonschema import Draft202012Validator


class SchemaError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("\n".join(errors))


@lru_cache(maxsize=4)
def _validator(name: str) -> Draft202012Validator:
    text = files("apple_fill.schemas").joinpath(name).read_text(encoding="utf-8")
    return Draft202012Validator(json.loads(text))


def schema_errors(instance: object, schema_name: str) -> list[str]:
    validator = _validator(schema_name)
    messages: list[str] = []
    for error in sorted(validator.iter_errors(instance), key=lambda item: list(item.path)):
        location = ".".join(str(part) for part in error.path) or "(root)"
        messages.append(f"{location}: {error.message}")
    return messages


def require_schema(instance: object, schema_name: str) -> None:
    errors = schema_errors(instance, schema_name)
    if errors:
        raise SchemaError(errors)
