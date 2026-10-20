"""Build an adapter from the form that is already on screen.

Only labels that match the product dictionary become fill steps. Checkboxes
and anything the dictionary does not name are left out.
"""

from __future__ import annotations

import json
import re
import urllib.request
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

from apple_fill.fields import SCALAR_FIELDS, SPEC_FIELDS

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")
_TEXT_TYPES = {"text", "textarea", "search", "email", "tel", "url", "number", "select"}
_PUNCT = re.compile(r"[\s:：*＊()（）]+")


class GenerateError(ValueError):
    pass


def _norm(text: str) -> str:
    return _PUNCT.sub("", text or "").casefold()


def _catalog() -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    for field in SCALAR_FIELDS:
        for label in field.labels:
            rows.append((label, f"fields.{field.key}", field.labels[0]))
    for spec in SPEC_FIELDS:
        for label in spec.labels:
            rows.append((label, f"fields.specs.{spec.key}", spec.label))
    rows.sort(key=lambda item: len(_norm(item[0])), reverse=True)
    return rows


def _adapter_id(host: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", host.lower()).strip("-")
    if not slug or slug[0].isdigit():
        slug = "site-" + slug
    return slug[:48].strip("-") or "site"


def _yaml_string(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_./:~?&=%+-]+", value):
        return value
    return json.dumps(value, ensure_ascii=False)


def _match_label(label: str, catalog: list[tuple[str, str, str]]) -> tuple[str, str] | None:
    wanted = _norm(label)
    if not wanted:
        return None
    for known, source, shown in catalog:
        if wanted == _norm(known):
            return source, shown
    return None


class _FormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.controls: list[dict[str, str]] = []
        self.row_label = ""
        self.inline_label = ""
        self.stack: list[dict[str, Any]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._start(tag, attrs)
        if tag == "input":
            self._close()

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._start(tag, attrs)
        self._close()

    def _start(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key: value or "" for key, value in attrs}
        frame: dict[str, Any] = {
            "tag": tag,
            "classes": set(attr.get("class", "").split()),
            "text": [],
            "inputs": [],
        }
        self.stack.append(frame)
        if tag not in {"input", "textarea", "select"}:
            return
        kind = "select" if tag == "select" else attr.get("type") or tag
        self.controls.append(
            {
                "name": attr.get("name", ""),
                "type": kind.lower(),
                "label": "",
                "row": self.row_label,
                "inline": self.inline_label,
            }
        )
        self.inline_label = ""
        index = len(self.controls) - 1
        frame["inputs"].append(index)
        if len(self.stack) > 1:
            self.stack[-2]["inputs"].append(index)

    def handle_data(self, data: str) -> None:
        if self.stack:
            self.stack[-1]["text"].append(data)

    def handle_endtag(self, tag: str) -> None:
        while self.stack:
            node = self.stack[-1]
            self._close()
            if node["tag"] == tag:
                break

    def _close(self) -> None:
        if not self.stack:
            return
        node = self.stack.pop()
        text = "".join(node["text"]).strip()
        if self.stack and text:
            self.stack[-1]["text"].append(text)
        if node["tag"] == "div" and "label" in node["classes"] and text:
            self.row_label = text
            self.inline_label = ""
        elif node["tag"] == "span" and text and not node["inputs"]:
            self.inline_label = text
        for index in node["inputs"]:
            control = self.controls[index]
            if text and not control["label"]:
                control["label"] = text

    def finish(self) -> list[dict[str, str]]:
        while self.stack:
            self._close()
        ready: list[dict[str, str]] = []
        for control in self.controls:
            if control["type"] in _TEXT_TYPES:
                label = control["inline"] or control["row"] or control["label"]
            else:
                label = control["label"] or control["row"]
            ready.append({"name": control["name"], "type": control["type"], "label": label})
        return ready


def controls_from_html(markup: str) -> list[dict[str, str]]:
    parser = _FormParser()
    parser.feed(markup)
    return parser.finish()


def controls_from_url(page_url: str) -> list[dict[str, str]]:
    parsed = urlparse(page_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise GenerateError("这一页读不到格子")
    request = urllib.request.Request(page_url, headers={"User-Agent": "apple-fill"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            raw = response.read(1_000_000)
            charset = response.headers.get_content_charset() or "utf-8"
    except Exception as exc:
        raise GenerateError("这一页读不到格子") from exc
    controls = controls_from_html(raw.decode(charset, errors="replace"))
    if not controls:
        raise GenerateError("这一页读不到格子")
    return controls


def render_adapter(page_url: str, controls: list[dict[str, Any]]) -> tuple[str, list[str]]:
    parsed = urlparse(page_url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or not host:
        raise GenerateError("这一页没有可保存的网址")
    catalog = _catalog()
    used: set[str] = set()
    matched: list[str] = []
    steps: list[str] = [
        "  - id: open",
        "    action: goto",
        f"    url: {_yaml_string(page_url)}",
    ]
    for control in controls:
        kind = str(control.get("type") or "text").lower()
        if kind not in _TEXT_TYPES:
            continue
        name = str(control.get("name") or "")
        if not _NAME.fullmatch(name):
            continue
        found = _match_label(str(control.get("label") or ""), catalog)
        if found is None or found[0] in used:
            continue
        source, shown = found
        used.add(source)
        matched.append(shown)
        step_id = "fill-" + name
        steps.extend(
            [
                f"  - id: {_yaml_string(step_id)}",
                "    action: fill",
                f"    name: {name}",
                f"    label: {_yaml_string(shown)}",
                f"    selector: {_yaml_string('[name=\"' + name + '\"]')}",
                f"    from: {source}",
            ]
        )
    if not matched:
        raise GenerateError("这一页没有对上能填的格子")
    lines = [
        f"id: {_adapter_id(host)}",
        "version: 1",
        f"entryUrl: {_yaml_string(page_url)}",
        "allowedHosts:",
        f"  - {host}",
        "steps:",
        *steps,
    ]
    return "\n".join(lines) + "\n", matched
