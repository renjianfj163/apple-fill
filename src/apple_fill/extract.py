"""Turn documents into product.json.

The label backend only copies text that sits after a known label. The model
backend may classify a derivable field as derived, and every sourced value is
checked against the text we read from the file.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from apple_fill.documents import LoadedDocument, load_documents
from apple_fill.fields import PROMPT_VERSION, SCALAR_FIELDS, SCALAR_KEYS, SCHEMA_VERSION, SPEC_FIELDS
from apple_fill.product import empty_field, empty_fields, require_product
from apple_fill.provenance import normalize_text


class ExtractError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("\n".join(errors))


@dataclass(frozen=True)
class Hit:
    value: str
    doc_id: str
    page: int
    quote: str


def _label_rows() -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    for spec in SCALAR_FIELDS:
        for label in spec.labels:
            rows.append((label, "scalar", spec.key))
    for spec in SPEC_FIELDS:
        for label in spec.labels:
            rows.append((label, "spec", spec.key))
    rows.sort(key=lambda item: len(item[0]), reverse=True)
    return rows


_LABELS = _label_rows()


def match_line(line: str) -> tuple[str, str, str, str] | None:
    normalized = unicodedata.normalize("NFKC", line).strip()
    for label, kind, key in _LABELS:
        matched = re.match(rf"^{re.escape(label)}\s*[:：]\s*(.+)$", normalized, flags=re.IGNORECASE)
        if not matched:
            continue
        value = matched.group(1).strip().strip("。；;")
        if value:
            return kind, key, value, line.strip()
    return None


def collect_hits(documents: list[LoadedDocument]) -> dict[tuple[str, str], list[Hit]]:
    found: dict[tuple[str, str], list[Hit]] = defaultdict(list)
    for document in documents:
        for page in document.pages:
            for line in page.text.splitlines():
                matched = match_line(line)
                if matched is None:
                    continue
                kind, key, value, quote = matched
                found[(kind, key)].append(Hit(value, document.doc_id, page.page, quote))
    return found


def _sources(hits: list[Hit]) -> list[dict[str, Any]]:
    return [{"docId": hit.doc_id, "page": hit.page, "quote": hit.quote} for hit in hits]


def fold_hits(hits: list[Hit]) -> dict[str, Any]:
    if not hits:
        return empty_field()
    groups: dict[str, list[Hit]] = defaultdict(list)
    for hit in hits:
        groups[normalize_text(hit.value)].append(hit)
    if len(groups) == 1:
        grouped = next(iter(groups.values()))
        return {"value": grouped[0].value, "status": "sourced", "sources": _sources(grouped)}
    candidates = []
    for index, grouped in enumerate(groups.values(), start=1):
        candidates.append(
            {
                "id": f"cand-{index}",
                "value": grouped[0].value,
                "sources": _sources(grouped),
            }
        )
    return {"value": None, "status": "conflict", "sources": [], "candidates": candidates}


def product_from_hits(
    documents: list[LoadedDocument],
    hits: dict[tuple[str, str], list[Hit]],
    *,
    product_id: str,
    backend: str,
    model: str | None = None,
) -> dict[str, Any]:
    fields = empty_fields()
    for key in SCALAR_KEYS:
        fields[key] = fold_hits(hits.get(("scalar", key), []))
    for spec in fields["specs"]:
        folded = fold_hits(hits.get(("spec", spec["key"]), []))
        spec.update(folded)
    extractor: dict[str, str] = {"backend": backend, "promptVersion": PROMPT_VERSION}
    if model:
        extractor["model"] = model
    product = {
        "schemaVersion": SCHEMA_VERSION,
        "productId": product_id,
        "extractedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "extractor": extractor,
        "documents": [document.as_dict() for document in documents],
        "fields": fields,
        "attachments": [
            {
                "role": "source",
                "docId": document.doc_id,
                "filename": document.filename,
                "sha256": document.sha256,
                "path": str(document.path),
            }
            for document in documents
        ],
    }
    text = {document.doc_id: document.text_map() for document in documents}
    require_product(product, text)
    return product


def extract_labels(paths: list[Path], product_id: str) -> dict[str, Any]:
    documents = load_documents(paths)
    return product_from_hits(documents, collect_hits(documents), product_id=product_id, backend="labels")


def text_corpus(documents: list[LoadedDocument]) -> dict[str, dict[int, str]]:
    return {document.doc_id: document.text_map() for document in documents}


def pages_for_prompt(documents: list[LoadedDocument]) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    for document in documents:
        for page in document.pages:
            pages.append(
                {
                    "docId": document.doc_id,
                    "filename": document.filename,
                    "page": page.page,
                    "text": page.text,
                    "textAvailable": bool(page.text.strip()),
                }
            )
    return pages


def cache_file(documents: list[LoadedDocument], model: str) -> Path:
    digest = hashlib.sha256()
    for document in documents:
        digest.update(document.sha256.encode("utf-8"))
        digest.update(str(len(document.pages)).encode("utf-8"))
    digest.update(PROMPT_VERSION.encode("utf-8"))
    digest.update(model.encode("utf-8"))
    directory = Path(os.environ.get("APPLE_FILL_CACHE", ".cache/apple-fill"))
    return directory / f"{digest.hexdigest()}.json"


def load_cached_output(documents: list[LoadedDocument], model: str) -> dict[str, Any] | None:
    path = cache_file(documents, model)
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def store_cached_output(documents: list[LoadedDocument], model: str, output: dict[str, Any]) -> None:
    path = cache_file(documents, model)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")


def extract_product(
    paths: list[Path],
    product_id: str,
    *,
    backend: str = "auto",
    llm: tuple[str, str, str] | None = None,
) -> dict[str, Any]:
    if backend not in {"auto", "labels", "llm"}:
        raise ExtractError([f"不认识的抽取方式：{backend}"])
    if llm is not None:
        url, model, api_key = (part.strip() for part in llm)
        if not url or not model:
            raise ExtractError(["使用模型抽取时需要接口地址和模型名"])
        from apple_fill.llm import extract_with_llm

        return extract_with_llm(paths, product_id, url=url, model=model, api_key=api_key)
    use_llm = backend == "llm" or (backend == "auto" and bool(os.environ.get("APPLE_FILL_LLM_URL")))
    if not use_llm:
        return extract_labels(paths, product_id)
    from apple_fill.llm import extract_with_llm

    return extract_with_llm(paths, product_id)
