"""Build and check product.json.

Semantic rules that JSON Schema cannot express live here: a sourced value must
be supported by its quote, a missing value is null, and a literal field cannot
be marked derived.
"""

from __future__ import annotations

from typing import Any

from apple_fill.fields import DERIVABLE_KEYS, SCALAR_KEYS, SPEC_FIELDS
from apple_fill.provenance import quote_in_page, value_supported_by_quote
from apple_fill.schema_io import require_schema


class ProductError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("\n".join(errors))


def empty_field() -> dict[str, Any]:
    return {"value": None, "status": "missing", "sources": []}


def empty_fields() -> dict[str, Any]:
    fields = {key: empty_field() for key in SCALAR_KEYS}
    fields["specs"] = [
        {
            "key": spec.key,
            "label": spec.label,
            "value": None,
            "status": "missing",
            "sources": [],
        }
        for spec in SPEC_FIELDS
    ]
    fields["attributes"] = []
    return fields


def page_index(documents_text: dict[str, dict[int, str]]) -> dict[tuple[str, int], str]:
    return {
        (doc_id, page): text
        for doc_id, pages in documents_text.items()
        for page, text in pages.items()
    }


def _value_matches_sources(value: str, sources: list[dict[str, Any]]) -> bool:
    return any(value_supported_by_quote(value, str(source.get("quote") or "")) for source in sources)


def _check_sources(sources: list[dict[str, Any]], pages: dict[tuple[str, int], str], where: str) -> list[str]:
    errors: list[str] = []
    if not sources:
        errors.append(f"{where}: sourced 字段缺少出处")
        return errors
    for index, source in enumerate(sources):
        key = (source.get("docId"), source.get("page"))
        page_text = pages.get(key)
        quote = str(source.get("quote") or "")
        if page_text is None:
            errors.append(f"{where}: 出处 {source.get('docId')} 第 {source.get('page')} 页不在本次文档里")
            continue
        if not quote_in_page(quote, page_text):
            errors.append(f"{where}: 引用不在原文第 {source.get('page')} 页")
    return errors


def _check_value_field(field: dict[str, Any], pages: dict[tuple[str, int], str], where: str, *, derivable: bool) -> list[str]:
    status = field.get("status")
    value = field.get("value")
    sources = field.get("sources") or []
    errors: list[str] = []
    if status == "missing":
        if value is not None or sources:
            errors.append(f"{where}: missing 的值必须为空，且不能带出处")
        return errors
    if status == "conflict":
        candidates = field.get("candidates") or []
        if value is not None:
            errors.append(f"{where}: conflict 在人工选定前值必须为空")
        if len(candidates) < 2:
            errors.append(f"{where}: conflict 至少要有两个候选")
        for candidate in candidates:
            candidate_sources = candidate.get("sources") or []
            errors.extend(_check_sources(candidate_sources, pages, f"{where}.{candidate.get('id')}"))
            if not _value_matches_sources(str(candidate.get("value") or ""), candidate_sources):
                errors.append(f"{where}.{candidate.get('id')}: 候选值和引用对不上")
        return errors
    if status == "derived":
        if not derivable:
            errors.append(f"{where}: 这个字段只能从原文抄写，不能推断")
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{where}: derived 必须给出待确认的值")
        return errors
    if status == "sourced":
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{where}: sourced 的值不能为空")
            return errors
        errors.extend(_check_sources(sources, pages, where))
        if sources and not _value_matches_sources(value, sources):
            errors.append(f"{where}: 值「{value}」不能从引用里得到")
        return errors
    errors.append(f"{where}: 未知状态 {status}")
    return errors


def semantic_errors(product: dict[str, Any], documents_text: dict[str, dict[int, str]]) -> list[str]:
    pages = page_index(documents_text)
    errors: list[str] = []
    fields = product.get("fields") or {}
    for key in SCALAR_KEYS:
        field = fields.get(key)
        if not isinstance(field, dict):
            errors.append(f"fields.{key}: 缺少字段")
            continue
        errors.extend(_check_value_field(field, pages, f"fields.{key}", derivable=key in DERIVABLE_KEYS))
    for spec in fields.get("specs") or []:
        errors.extend(
            _check_value_field(spec, pages, f"fields.specs.{spec.get('key')}", derivable=False)
        )
    for attribute in fields.get("attributes") or []:
        errors.extend(
            _check_value_field(
                attribute,
                pages,
                f"fields.attributes.{attribute.get('key')}",
                derivable=False,
            )
        )
    return errors


def require_product(product: dict[str, Any], documents_text: dict[str, dict[int, str]]) -> None:
    require_schema(product, "product.schema.json")
    errors = semantic_errors(product, documents_text)
    if errors:
        raise ProductError(errors)


def lookup_field(product: dict[str, Any], path: str) -> dict[str, Any] | None:
    """Resolve fields.productName or fields.specs.ratedVoltage."""
    parts = path.split(".")
    if len(parts) < 2 or parts[0] != "fields":
        raise ProductError([f"字段路径要以 fields. 开头：{path}"])
    fields = product["fields"]
    key = parts[1]
    if key in SCALAR_KEYS and len(parts) == 2:
        return fields[key]
    if key in {"specs", "attributes"} and len(parts) == 3:
        wanted = parts[2]
        for item in fields[key]:
            if item["key"] == wanted:
                return item
        return None
    raise ProductError([f"不认识的字段路径：{path}"])
