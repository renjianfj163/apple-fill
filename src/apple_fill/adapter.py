"""Load a site adapter. Submit is not an action this file can express."""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import yaml

from apple_fill.schema_io import require_schema

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")
_TRANSFORMS = {
    "trim": str.strip,
    "upper": str.upper,
    "lower": str.lower,
}


class AdapterError(ValueError):
    pass


@dataclass(frozen=True)
class Step:
    id: str
    action: str
    url: str | None = None
    label: str | None = None
    selector: str | None = None
    name: str | None = None
    source: str | None = None
    from_attachment: str | None = None
    transform: tuple[str, ...] = ()
    required: bool = False
    value_map: dict[str, str] | None = None


@dataclass(frozen=True)
class Adapter:
    id: str
    version: int
    entry_url: str
    allowed_hosts: frozenset[str]
    resource_hosts: frozenset[str]
    ignore_controls: frozenset[str]
    session_ttl_seconds: int | None
    steps: tuple[Step, ...]
    path: Path
    sha256: str


def apply_transforms(value: str, names: tuple[str, ...]) -> str:
    current = value
    for name in names:
        current = _TRANSFORMS[name](current)
    return current


def navigation_hosts(adapter: Adapter) -> frozenset[str]:
    configured = os.environ.get("APPLE_FILL_ALLOWED_HOSTS", "")
    if not configured.strip():
        return adapter.allowed_hosts
    platform = {item.strip().lower() for item in configured.split(",") if item.strip()}
    allowed = adapter.allowed_hosts & platform
    if not allowed:
        raise AdapterError("适配包主机和 APPLE_FILL_ALLOWED_HOSTS 没有交集，已拒绝打开页面")
    return frozenset(allowed)


def resource_hosts(adapter: Adapter, navigate: frozenset[str]) -> frozenset[str]:
    configured = os.environ.get("APPLE_FILL_RESOURCE_HOSTS", "")
    extra = adapter.resource_hosts
    if configured.strip():
        platform = {item.strip().lower() for item in configured.split(",") if item.strip()}
        extra = extra & platform
    return navigate | extra


def assert_request_allowed(url: str, *, navigation: bool, navigate: frozenset[str], resources: frozenset[str]) -> None:
    parsed = urlparse(url)
    if parsed.scheme in {"data", "blob", "about"}:
        return
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise AdapterError(f"只允许打开 http 或 https：{url}")
    host = parsed.hostname.lower()
    pool = navigate if navigation else resources
    if host not in pool:
        kind = "页面" if navigation else "资源"
        raise AdapterError(f"{kind}主机不在白名单里：{host}")


def _step(raw: dict) -> Step:
    action = raw["action"]
    name = raw.get("name")
    if name and not _NAME.fullmatch(name):
        raise AdapterError(f"步骤 {raw['id']} 的控件名只能用字母、数字、下划线和短横线")
    step = Step(
        id=raw["id"],
        action=action,
        url=raw.get("url"),
        label=raw.get("label"),
        selector=raw.get("selector"),
        name=name,
        source=raw.get("from"),
        from_attachment=raw.get("fromAttachment"),
        transform=tuple(raw.get("transform") or ()),
        required=bool(raw.get("required", False)),
        value_map=dict(raw["map"]) if raw.get("map") else None,
    )
    if action == "goto" and not (step.url):
        return step
    if action in {"fill", "select"} and (not step.source or not step.name or not (step.label or step.selector)):
        raise AdapterError(f"步骤 {step.id} 需要 from、name，以及 label 或 selector")
    if action == "upload" and (not step.from_attachment or not step.name or not (step.label or step.selector)):
        raise AdapterError(f"步骤 {step.id} 需要 fromAttachment、name，以及 label 或 selector")
    if action in {"click", "wait_for"} and not step.selector:
        raise AdapterError(f"步骤 {step.id} 需要 selector")
    return step


def load_adapter(path: Path, *, base_url: str | None = None) -> Adapter:
    raw_bytes = path.read_bytes()
    parsed = yaml.safe_load(raw_bytes.decode("utf-8"))
    require_schema(parsed, "adapter.schema.json")
    ids = [step["id"] for step in parsed["steps"]]
    if len(ids) != len(set(ids)):
        raise AdapterError("步骤 id 不能重复")
    entry = _render(parsed["entryUrl"], base_url)
    steps = tuple(_render_step(_step(item), base_url) for item in parsed["steps"])
    adapter = Adapter(
        id=parsed["id"],
        version=int(parsed["version"]),
        entry_url=entry,
        allowed_hosts=frozenset(host.lower() for host in parsed["allowedHosts"]),
        resource_hosts=frozenset(host.lower() for host in parsed.get("resourceHosts") or []),
        ignore_controls=frozenset(parsed.get("ignoreControls") or []),
        session_ttl_seconds=parsed.get("sessionTtlSeconds"),
        steps=steps,
        path=path.resolve(),
        sha256=hashlib.sha256(raw_bytes).hexdigest(),
    )
    navigate = navigation_hosts(adapter)
    assert_request_allowed(adapter.entry_url, navigation=True, navigate=navigate, resources=navigate)
    for step in adapter.steps:
        if step.action == "goto" and step.url:
            assert_request_allowed(step.url, navigation=True, navigate=navigate, resources=navigate)
    return adapter


def _render(value: str, base_url: str | None) -> str:
    if "{{baseUrl}}" not in value:
        return value
    if not base_url:
        raise AdapterError("地址里有 {{baseUrl}}。本地演示会自动填写；正式站点请写成完整入口地址。")
    return value.replace("{{baseUrl}}", base_url.rstrip("/"))


def _render_step(step: Step, base_url: str | None) -> Step:
    if not step.url:
        return step
    return Step(**{**step.__dict__, "url": _render(step.url, base_url)})
