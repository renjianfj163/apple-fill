"""Replay an adapter up to the submit button, then stop."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from apple_fill.adapter import (
    Adapter,
    AdapterError,
    Step,
    apply_transforms,
    assert_request_allowed,
    navigation_hosts,
    resource_hosts,
)
from apple_fill.documents import read_pages
from apple_fill.handoff import write_handoff
from apple_fill.product import ProductError, lookup_field, require_product

_SUBMIT = re.compile(r"提交|申报|送出|确认提交|立即申请|submit", re.IGNORECASE)
_DECLARATION = re.compile(r"保证属实|真实有效|本人承诺|法律责任|真实性")
_TIMEOUT_MS = 15_000

READBACK_JS = """
() => {
  const out = [];
  const nodes = Array.from(document.querySelectorAll("input, select, textarea"));
  nodes.forEach((el, index) => {
    const tag = el.tagName.toLowerCase();
    const type = tag === "input" ? (el.type || "text").toLowerCase() : tag;
    let value = null;
    let checked = null;
    let display = null;
    let files = 0;
    if (type === "checkbox" || type === "radio") {
      checked = Boolean(el.checked);
      value = el.value;
    } else if (type === "file") {
      files = el.files ? el.files.length : 0;
      value = files ? Array.from(el.files).map((file) => file.name).join(", ") : "";
    } else if (tag === "select") {
      value = el.value;
      const option = el.selectedOptions && el.selectedOptions[0];
      display = option ? option.label : "";
    } else {
      value = el.value;
    }
    out.push({
      index,
      name: el.name || "",
      id: el.id || "",
      type,
      value,
      display,
      checked,
      files,
      hidden: type === "hidden" || el.closest("[hidden]") !== null,
      disabled: Boolean(el.disabled)
    });
  });
  return out;
}
"""


class UnsafeAction(RuntimeError):
    pass


@dataclass
class FillResult:
    out_dir: Path
    handoff_path: Path
    clicked_submit: bool
    page_submitted: bool
    actions: list[str] = field(default_factory=list)
    mismatches: list[str] = field(default_factory=list)


def reload_product_text(product: dict[str, Any]) -> dict[str, dict[int, str]]:
    text: dict[str, dict[int, str]] = {}
    for document in product.get("documents") or []:
        path = Path(str(document.get("path") or ""))
        if not path.is_file():
            raise ProductError([f"资料不在原路径，无法核对出处：{document.get('filename')}"])
        pages = read_pages(path, str(document["docId"]))
        text[str(document["docId"])] = {page.page: page.text for page in pages}
    return text


def snapshot_hash(controls: list[dict[str, Any]], ignored: frozenset[str]) -> str:
    rows = []
    for control in controls:
        if control.get("name") in ignored:
            continue
        rows.append(
            {
                "checked": control.get("checked"),
                "id": control.get("id") or "",
                "name": control.get("name") or "",
                "type": control.get("type") or "",
                "value": control.get("value"),
            }
        )
    rows.sort(key=lambda item: (item["name"], item["id"], item["type"]))
    payload = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def forbidden_click(control_type: str, text: str) -> str | None:
    if control_type.lower() == "submit":
        return "提交按钮不会自动点击"
    if _SUBMIT.search(text or ""):
        return "这一步的文字像提交，已停住"
    if _DECLARATION.search(text or ""):
        return "真实性声明需要操作人自己勾选"
    return None


def _attachment(product: dict[str, Any], token: str) -> dict[str, Any] | None:
    matches = [
        item
        for item in product.get("attachments") or []
        if token in {item.get("role"), item.get("filename"), item.get("docId")}
    ]
    if len(matches) == 1:
        return matches[0]
    return None


def _locator(page, step: Step):
    if step.label:
        located = page.get_by_label(step.label, exact=True)
        if located.count() == 1:
            return located
    if step.selector:
        located = page.locator(step.selector)
        if located.count() == 1:
            return located
    raise AdapterError(f"步骤 {step.id} 找不到唯一控件")


def _control_by_name(page, name: str) -> dict[str, Any] | None:
    located = page.locator(f"[name='{name}']")
    if located.count() != 1:
        return None
    return located.evaluate(
        """el => {
          const tag = el.tagName.toLowerCase();
          const type = tag === "input" ? (el.type || "text").toLowerCase() : tag;
          if (type === "file") {
            const files = el.files ? el.files.length : 0;
            return { type, value: files ? "1" : "", files, checked: null };
          }
          if (type === "checkbox" || type === "radio") {
            return { type, value: el.value, files: 0, checked: Boolean(el.checked) };
          }
          return { type, value: el.value, files: 0, checked: null };
        }"""
    )


def _values_match(expected: str, actual: str) -> bool:
    from apple_fill.provenance import normalize_text

    return normalize_text(expected) == normalize_text(actual or "")


def fill_form(
    product: dict[str, Any],
    adapter: Adapter,
    out_dir: Path,
    *,
    headed: bool = False,
    pause: bool = False,
) -> FillResult:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("还没有安装 Playwright。请先运行：python -m playwright install chromium") from exc

    text = reload_product_text(product)
    require_product(product, text)
    navigate = navigation_hosts(adapter)
    resources = resource_hosts(adapter, navigate)
    out_dir.mkdir(parents=True, exist_ok=True)
    actions: list[str] = []
    mismatches: list[str] = []
    skipped: list[dict[str, str]] = []
    filled: list[dict[str, Any]] = []
    origins: dict[str, str] = {}

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not headed)
        page = browser.new_page()
        page.set_default_timeout(_TIMEOUT_MS)

        def guard(route) -> None:
            request = route.request
            navigation = request.is_navigation_request() and request.frame == page.main_frame
            try:
                assert_request_allowed(request.url, navigation=navigation, navigate=navigate, resources=resources)
            except AdapterError:
                route.abort()
                return
            route.continue_()

        page.route("**/*", guard)
        try:
            _run_steps(
                page,
                product,
                adapter,
                actions=actions,
                mismatches=mismatches,
                skipped=skipped,
                filled=filled,
                origins=origins,
            )
            controls = page.evaluate(READBACK_JS)
            _assign_origins(controls, origins, adapter.ignore_controls)
            screenshot = out_dir / "page.png"
            page.screenshot(path=str(screenshot), full_page=True)
            page_submitted = bool(page.evaluate("() => Boolean(window.__submitted)"))
            payload = _payload(
                product,
                adapter,
                filled=filled,
                skipped=skipped,
                controls=controls,
                mismatches=mismatches,
                screenshot=screenshot.name,
                page_submitted=page_submitted,
            )
            handoff_path, _html_path = write_handoff(out_dir, payload)
            if pause:
                print(f"已停在提交前。请在浏览器里核对并自行提交。核对页：{handoff_path.parent / 'handoff.html'}")
                input("看完后回到这里按 Enter，浏览器才会关闭。")
            elif page_submitted:
                raise UnsafeAction("页面显示已经提交，自动填写必须停在提交之前")
        finally:
            browser.close()

    return FillResult(
        out_dir=out_dir,
        handoff_path=handoff_path,
        clicked_submit=False,
        page_submitted=page_submitted,
        actions=actions,
        mismatches=mismatches,
    )


def _run_steps(page, product, adapter: Adapter, *, actions, mismatches, skipped, filled, origins) -> None:
    for step in adapter.steps:
        if step.action == "goto":
            url = step.url or adapter.entry_url
            page.goto(url, wait_until="domcontentloaded")
            actions.append(f"goto:{urlparse(url).netloc}")
            continue
        if step.action == "wait_for":
            page.locator(step.selector or "").wait_for(state="visible")
            actions.append(f"wait:{step.id}")
            continue
        if step.action == "click":
            _click(page, step)
            actions.append(f"click:{step.id}")
            continue
        if step.action in {"fill", "select"}:
            _fill_or_select(page, product, step, filled=filled, skipped=skipped, mismatches=mismatches, origins=origins)
            actions.append(f"{step.action}:{step.id}")
            continue
        if step.action == "upload":
            _upload(page, product, step, filled=filled, skipped=skipped, mismatches=mismatches, origins=origins)
            actions.append(f"upload:{step.id}")
            continue
        raise UnsafeAction(f"步骤 {step.id} 的动作不能执行：{step.action}")


def _click(page, step: Step) -> None:
    located = page.locator(step.selector or "")
    if located.count() != 1:
        raise AdapterError(f"步骤 {step.id} 的点击目标不是唯一控件")
    info = located.evaluate(
        """el => ({
          type: (el.getAttribute("type") || "").toLowerCase(),
          text: (el.innerText || el.value || "").trim()
        })"""
    )
    reason = forbidden_click(str(info.get("type") or ""), str(info.get("text") or ""))
    if reason:
        raise UnsafeAction(f"{step.id}: {reason}")
    located.click()


def _planned_value(product: dict[str, Any], step: Step, skipped: list[dict[str, str]]) -> str | None:
    field = lookup_field(product, step.source or "")
    path = step.source or step.id
    if field is None or field.get("status") != "sourced" or not field.get("value"):
        status = "缺少" if field is None else str(field.get("status"))
        reason = {
            "missing": "资料里没有这个值，已留空",
            "conflict": "资料互相矛盾，需要你在页面上决定",
            "derived": "这个值是推断的，需要你自己确认后再填",
            "缺少": "适配包里的字段在资料中不存在",
        }.get(status, "这个值没有可抄写的原文")
        if field and field.get("status") == "conflict":
            choices = "；".join(item.get("value", "") for item in field.get("candidates") or [])
            reason = f"资料互相矛盾（{choices}），需要你在页面上决定"
        skipped.append({"path": path, "reason": reason})
        return None
    value = str(field["value"])
    value = apply_transforms(value, step.transform)
    if step.value_map is not None:
        if value not in step.value_map:
            skipped.append({"path": path, "reason": f"值「{value}」不在适配包的选项映射里"})
            return None
        value = step.value_map[value]
    return value


def _fill_or_select(page, product, step: Step, *, filled, skipped, mismatches, origins) -> None:
    value = _planned_value(product, step, skipped)
    if value is None:
        return
    located = _locator(page, step)
    if step.action == "select":
        options = located.evaluate(
            """el => Array.from(el.options || []).map(option => ({value: option.value, label: option.label}))"""
        )
        wanted = next(
            (
                option["value"]
                for option in options
                if value in {option.get("value"), option.get("label")}
            ),
            None,
        )
        if wanted is None:
            skipped.append({"path": step.source or step.id, "reason": f"下拉框里没有「{value}」"})
            return
        located.select_option(value=wanted)
        expected = wanted
    else:
        located.fill(value)
        expected = value
    origins[step.name or ""] = step.id
    actual = _control_by_name(page, step.name or "")
    if actual is None or not _values_match(expected, str(actual.get("value") or "")):
        mismatches.append(f"{step.id}: 页面读回的值不是「{expected}」")
    else:
        field = lookup_field(product, step.source or "")
        filled.append(
            {
                "path": step.source,
                "label": step.label or step.name,
                "value": expected,
                "sources": (field or {}).get("sources") or [],
            }
        )


def _upload(page, product, step: Step, *, filled, skipped, mismatches, origins) -> None:
    attachment = _attachment(product, step.from_attachment or "")
    if attachment is None or not Path(str(attachment.get("path") or "")).is_file():
        skipped.append({"path": step.from_attachment or step.id, "reason": "找不到要上传的文件"})
        return
    path = Path(str(attachment["path"]))
    _locator(page, step).set_input_files(str(path))
    origins[step.name or ""] = step.id
    actual = _control_by_name(page, step.name or "")
    if actual is None or int(actual.get("files") or 0) < 1:
        mismatches.append(f"{step.id}: 上传后页面里没有文件")
    else:
        filled.append(
            {
                "path": step.from_attachment,
                "label": step.label or step.name,
                "value": path.name,
                "sources": [],
            }
        )


def _assign_origins(controls: list[dict[str, Any]], origins: dict[str, str], ignored: frozenset[str]) -> None:
    for control in controls:
        name = control.get("name") or ""
        if name in ignored:
            control["origin"] = "ignored"
        elif name in origins:
            control["origin"] = f"step:{origins[name]}"
        else:
            control["origin"] = "unknown"


def _nonempty(control: dict[str, Any]) -> bool:
    if control.get("type") in {"checkbox", "radio"}:
        return bool(control.get("checked"))
    return bool(str(control.get("value") or "").strip())


def _payload(product, adapter: Adapter, *, filled, skipped, controls, mismatches, screenshot, page_submitted) -> dict[str, Any]:
    review = [
        control
        for control in controls
        if control.get("origin") == "unknown" and _nonempty(control)
    ]
    product_bytes = json.dumps(product, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        "stoppedBeforeSubmit": True,
        "clickedSubmit": False,
        "pageSubmitted": page_submitted,
        "productId": product.get("productId"),
        "productHash": hashlib.sha256(product_bytes).hexdigest(),
        "adapterId": adapter.id,
        "adapterVersion": adapter.version,
        "adapterHash": adapter.sha256,
        "formSnapshotHash": snapshot_hash(controls, adapter.ignore_controls),
        "sessionTtlSeconds": adapter.session_ttl_seconds,
        "screenshot": screenshot,
        "filled": filled,
        "skipped": skipped,
        "mismatches": mismatches,
        "needsOperatorReview": review,
        "readback": {"controls": controls},
        "documentsWithoutText": [
            document["filename"]
            for document in product.get("documents") or []
            if not document.get("textAvailable")
        ],
    }
