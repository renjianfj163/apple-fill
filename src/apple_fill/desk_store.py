"""Local case files for the desk: adapters, runs, and the panel summary."""

from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from apple_fill.adapter import load_adapter
from apple_fill.fields import SCALAR_FIELDS, SPEC_FIELDS

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def data_root() -> Path:
    override = os.environ.get("APPLE_FILL_HOME", "").strip()
    if override:
        return Path(override)
    appdata = os.environ.get("APPDATA", "").strip()
    if appdata:
        return Path(appdata) / "AppleFill"
    return Path.home() / ".apple-fill"


def read_brief(path: Path, source: str) -> dict[str, Any] | None:
    try:
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    if not isinstance(parsed, dict) or not parsed.get("id"):
        return None
    hosts = parsed.get("allowedHosts") or []
    if not isinstance(hosts, list) or not hosts:
        return None
    try:
        version = int(parsed.get("version") or 0)
    except (TypeError, ValueError):
        return None
    return {
        "id": str(parsed["id"]),
        "version": version,
        "allowedHosts": [str(host) for host in hosts],
        "entryUrl": str(parsed.get("entryUrl") or ""),
        "source": source,
        "path": str(path),
    }


def list_adapters(root: Path, bundled: list[Path]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for folder in bundled:
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*/adapter.yaml")):
            brief = read_brief(path, "bundled")
            if brief:
                found.append(brief)
    user = root / "adapters"
    if user.is_dir():
        for path in sorted(user.glob("*/*/adapter.yaml")):
            brief = read_brief(path, "user")
            if brief:
                found.append(brief)
    return found


def match_host(host: str, briefs: list[dict[str, Any]]) -> dict[str, Any] | None:
    wanted = host.lower()
    user = [item for item in briefs if item.get("source") == "user"]
    bundled = [item for item in briefs if item.get("source") != "user"]
    for brief in user + bundled:
        hosts = [str(item).lower() for item in brief.get("allowedHosts") or []]
        if wanted in hosts:
            return brief
    return None


def import_adapter(root: Path, text: str) -> dict[str, Any]:
    folder = root / "adapters" / "_incoming"
    folder.mkdir(parents=True, exist_ok=True)
    temporary = folder / "adapter.yaml"
    temporary.write_text(text, encoding="utf-8")
    try:
        loaded = load_adapter(temporary)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    safe_id = _SAFE.sub("-", loaded.id).strip("-") or "adapter"
    target = root / "adapters" / safe_id / f"v{loaded.version}"
    target.mkdir(parents=True, exist_ok=True)
    destination = target / "adapter.yaml"
    destination.write_text(text, encoding="utf-8")
    temporary.unlink(missing_ok=True)
    brief = read_brief(destination, "user")
    if brief is None:
        raise ValueError("适配包没有写入")
    return brief


def run_dir(root: Path, product_id: str) -> Path:
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    safe = _SAFE.sub("-", product_id).strip("-") or "case"
    path = root / "runs" / f"{stamp}_{safe}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _reason(status: str) -> str:
    if status == "conflict":
        return "两份文件不一致"
    if status == "derived":
        return "只能推断，原文没有写"
    return "资料里没有"


def panel_summary(product: dict[str, Any]) -> dict[str, Any]:
    fields = product.get("fields") or {}
    sourced = 0
    blanks: list[dict[str, str]] = []
    labels = {field.key: field.labels[0] for field in SCALAR_FIELDS}
    for key, label in labels.items():
        field = fields.get(key) or {}
        status = str(field.get("status") or "missing")
        if status == "sourced" and field.get("value"):
            sourced += 1
        elif status != "sourced":
            blanks.append({"field": label, "reason": _reason(status)})
    for spec in SPEC_FIELDS:
        match = next((item for item in fields.get("specs") or [] if item.get("key") == spec.key), {})
        status = str(match.get("status") or "missing")
        if status == "sourced" and match.get("value"):
            sourced += 1
        elif status != "sourced":
            blanks.append({"field": spec.label, "reason": _reason(status)})
    return {"sourced": sourced, "blanks": blanks}
