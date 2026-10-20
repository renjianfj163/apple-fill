"""Optional internal model.

Set APPLE_FILL_LLM_URL to an OpenAI-compatible chat completions endpoint,
and APPLE_FILL_LLM_MODEL to the model name. Temperature is always 0.
Sourced values that the quote check rejects are not kept.
"""

from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from apple_fill.extract import (
    ExtractError,
    cache_file,
    load_cached_output,
    load_documents,
    pages_for_prompt,
    store_cached_output,
    text_corpus,
)
from apple_fill.fields import DERIVABLE_KEYS, PROMPT_VERSION, SCALAR_FIELDS, SCALAR_KEYS, SCHEMA_VERSION, SPEC_FIELDS
from apple_fill.product import empty_field, empty_fields, require_product, semantic_errors

SYSTEM_PROMPT = f"""你是产品资料抽取器。promptVersion={PROMPT_VERSION}。
只根据给定页面文本输出一个 JSON 对象，不要输出 Markdown。
每个字面字段只能是 sourced、missing 或 conflict。
sourced 的 value 必须能从 quote 原文整段得到，quote 必须是页面里的原句。
资料里没有的值用 status=missing、value=null、sources=[]。
不要猜测、不要换算、不要翻译出资料里没有的句子。
只有这些键可以是 derived：{sorted(DERIVABLE_KEYS)}。derived 表示这句话不是原文，需要人确认。
字面字段禁止用 derived。
JSON 形状：
{{
  "fields": {{
    "productName": {{"value": "", "status": "sourced", "sources": [{{"docId": "d1", "page": 1, "quote": ""}}]}},
    "specs": [{{"key": "ratedVoltage", "label": "额定电压", "value": "", "status": "sourced", "sources": []}}]
  }}
}}
fields 里要包含这些键：{list(SCALAR_KEYS)}，再加上 specs。
"""


def _endpoint() -> tuple[str, str]:
    url = os.environ.get("APPLE_FILL_LLM_URL", "").strip()
    model = os.environ.get("APPLE_FILL_LLM_MODEL", "").strip()
    if not url or not model:
        raise ExtractError(["使用模型抽取时需要同时设置 APPLE_FILL_LLM_URL 和 APPLE_FILL_LLM_MODEL"])
    return url, model


def chat(
    messages: list[dict[str, str]],
    *,
    url: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
) -> str:
    if not url or not model:
        url, model = _endpoint()
        if api_key is None:
            api_key = os.environ.get("APPLE_FILL_LLM_API_KEY", "").strip()
    body: dict[str, Any] = {"model": model, "temperature": 0, "messages": messages}
    headers = {"Content-Type": "application/json"}
    api_key = (api_key or "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return str(payload["choices"][0]["message"]["content"])


def parse_model_json(content: str) -> dict[str, Any]:
    start = content.find("{")
    end = content.rfind("}")
    if start < 0 or end <= start:
        raise ExtractError(["模型没有返回 JSON"])
    try:
        parsed = json.loads(content[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ExtractError([f"模型返回的 JSON 无法解析：{exc}"]) from exc
    if not isinstance(parsed, dict):
        raise ExtractError(["模型返回的 JSON 必须是对象"])
    return parsed


def _overlay_field(raw: object, *, derivable: bool, key: str) -> dict[str, Any]:
    if raw is None:
        return empty_field()
    if not isinstance(raw, dict):
        raise ExtractError([f"{key}: 模型返回的字段不是对象"])
    status = raw.get("status")
    if status not in {"sourced", "missing", "conflict", "derived"}:
        raise ExtractError([f"{key}: 模型返回了无法识别的 status"])
    if status == "derived" and not derivable:
        raise ExtractError([f"{key}: 这个字段只能从原文抄写，模型不能标成推断"])
    field = empty_field()
    field["status"] = status
    field["value"] = raw.get("value")
    field["sources"] = raw.get("sources") or []
    if status == "conflict":
        field["candidates"] = raw.get("candidates") or []
    return field


def model_output_to_product(
    output: dict[str, Any],
    documents,
    *,
    product_id: str,
    model: str,
) -> dict[str, Any]:
    raw_fields = output.get("fields")
    if not isinstance(raw_fields, dict):
        raise ExtractError(["模型 JSON 缺少 fields 对象"])
    fields = empty_fields()
    derivable = set(DERIVABLE_KEYS)
    for key in SCALAR_KEYS:
        fields[key] = _overlay_field(raw_fields.get(key), derivable=key in derivable, key=key)
    raw_specs = {item.get("key"): item for item in raw_fields.get("specs") or [] if isinstance(item, dict)}
    for spec in fields["specs"]:
        if spec["key"] in raw_specs:
            overlaid = _overlay_field(raw_specs[spec["key"]], derivable=False, key=spec["key"])
            spec.update(overlaid)
    known_spec_keys = {spec.key for spec in SPEC_FIELDS}
    for key, raw in raw_specs.items():
        if key in known_spec_keys or not isinstance(raw, dict):
            continue
        overlaid = _overlay_field(raw, derivable=False, key=str(key))
        fields["attributes"].append(
            {
                "key": str(key),
                "label": str(raw.get("label") or key),
                **overlaid,
            }
        )
    product = {
        "schemaVersion": SCHEMA_VERSION,
        "productId": product_id,
        "extractedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "extractor": {"backend": "llm", "promptVersion": PROMPT_VERSION, "model": model},
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
    text = text_corpus(documents)
    errors = semantic_errors(product, text)
    if errors:
        raise ExtractError(errors)
    require_product(product, text)
    return product


def _user_payload(documents, product_id: str) -> str:
    catalog = {
        "scalar": [
            {"key": field.key, "labels": list(field.labels), "derivable": field.derivable}
            for field in SCALAR_FIELDS
        ],
        "specs": [{"key": field.key, "label": field.label, "labels": list(field.labels)} for field in SPEC_FIELDS],
    }
    return json.dumps(
        {"productId": product_id, "catalog": catalog, "pages": pages_for_prompt(documents)},
        ensure_ascii=False,
    )


def extract_with_llm(
    paths: list[Path],
    product_id: str,
    *,
    url: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
):
    if not url or not model:
        url, model = _endpoint()
        if api_key is None:
            api_key = os.environ.get("APPLE_FILL_LLM_API_KEY", "").strip()
    documents = load_documents(paths)
    cached = load_cached_output(documents, model)
    if cached is not None:
        return model_output_to_product(cached, documents, product_id=product_id, model=model)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": _user_payload(documents, product_id)},
    ]
    output = parse_model_json(chat(messages, url=url, model=model, api_key=api_key))
    try:
        product = model_output_to_product(output, documents, product_id=product_id, model=model)
    except ExtractError as first:
        messages.append({"role": "assistant", "content": json.dumps(output, ensure_ascii=False)})
        messages.append({"role": "user", "content": "这些字段没有通过校验，请只修正 JSON：\n" + "\n".join(first.errors)})
        output = parse_model_json(chat(messages, url=url, model=model, api_key=api_key))
        product = model_output_to_product(output, documents, product_id=product_id, model=model)
    store_cached_output(documents, model, output)
    return product


def cache_key_path(documents, model: str) -> Path:
    return cache_file(documents, model)
