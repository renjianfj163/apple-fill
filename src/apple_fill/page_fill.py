"""Fill only the page that is on screen.

The person turns the pages. Each click plans values for the visible fields
and writes those fields. Nothing here clicks Next or Submit.
"""

from __future__ import annotations

import queue
import threading
from typing import Any
from urllib.parse import urlparse

from apple_fill.generate_adapter import _catalog, _match_label

_TEXT_TYPES = {"text", "textarea", "search", "email", "tel", "url", "number", "select"}

_DOM = r"""
function typeOf(node) {
  const tag = node.tagName.toLowerCase();
  if (tag === "select") return "select";
  if (tag === "textarea") return "textarea";
  return (node.type || "text").toLowerCase();
}
function textOnly(el) {
  let out = "";
  for (const child of el.childNodes) {
    if (child.nodeType === 3) out += child.textContent || "";
  }
  const text = out.trim() || (el.innerText || el.textContent || "");
  return text.replace(/\s+/g, " ").trim();
}
function labelOf(node) {
  const direct = (node.getAttribute("aria-label") || "").trim();
  if (direct) return direct;
  const prev = node.previousElementSibling;
  if (prev && prev.classList && prev.classList.contains("inline-label")) {
    return textOnly(prev);
  }
  const doc = node.ownerDocument;
  if (node.id) {
    const labels = doc.querySelectorAll("label");
    for (const label of labels) {
      if (label.getAttribute("for") === node.id && textOnly(label)) return textOnly(label);
    }
  }
  const wrap = node.closest("label");
  if (wrap && textOnly(wrap)) return textOnly(wrap);
  const row = node.closest(".row");
  if (row) {
    const lab = row.querySelector(".label");
    if (lab && textOnly(lab)) return textOnly(lab);
  }
  return (node.getAttribute("placeholder") || "").replace(/\s+/g, " ").trim();
}
function isShown(node) {
  if (!node || node.disabled) return false;
  if (node.closest("[hidden]")) return false;
  const view = node.ownerDocument.defaultView;
  const style = view.getComputedStyle(node);
  if (style.display === "none" || style.visibility === "hidden") return false;
  const rect = node.getBoundingClientRect();
  return rect.width >= 1 && rect.height >= 1;
}
function visibleTextNodes() {
  const allowed = { text: 1, textarea: 1, search: 1, email: 1, tel: 1, url: 1, number: 1, select: 1 };
  const nodes = Array.from(document.querySelectorAll("input, textarea, select"));
  return nodes.filter((node) => allowed[typeOf(node)] && isShown(node));
}
function watchFocus() {
  if (window.__appleFillWatch) return;
  window.__appleFillWatch = true;
  window.__appleFillFocus = "";
  function note(node) {
    if (!node || !node.getAttribute) return;
    const path = node.getAttribute("data-apple-fill") || "";
    if (path) window.__appleFillFocus = path;
  }
  document.addEventListener("focusin", (event) => note(event.target), true);
  document.addEventListener("pointerdown", (event) => note(event.target), true);
}
function writeNode(node, item) {
  const value = String(item.value == null ? "" : item.value);
  const tag = node.tagName.toLowerCase();
  if (tag === "select") {
    const options = Array.from(node.options || []);
    const found = options.find((option) => option.value === value || option.label === value || option.text === value);
    if (!found) return { ok: false, reason: "missing-option", value: node.value, expected: value };
    if (node.value !== found.value) {
      node.value = found.value;
      node.dispatchEvent(new Event("input", { bubbles: true }));
      node.dispatchEvent(new Event("change", { bubbles: true }));
    }
    return { ok: node.value === found.value, value: node.value, expected: found.value };
  }
  const view = node.ownerDocument.defaultView;
  const proto = tag === "textarea" ? view.HTMLTextAreaElement.prototype : view.HTMLInputElement.prototype;
  const desc = Object.getOwnPropertyDescriptor(proto, "value");
  if (String(node.value || "") !== value) {
    if (desc && desc.set) desc.set.call(node, value);
    else node.value = value;
    node.dispatchEvent(new Event("input", { bubbles: true }));
    node.dispatchEvent(new Event("change", { bubbles: true }));
  }
  const actual = node.value == null ? "" : String(node.value);
  return { ok: actual.trim() === value.trim(), value: actual, expected: value };
}
"""

LIST_JS = "() => {\n" + _DOM + """
  return visibleTextNodes().map((node) => ({
    name: node.getAttribute("name") || "",
    label: labelOf(node),
    type: typeOf(node)
  }));
}
"""

APPLY_JS = "(items) => {\n" + _DOM + """
  const nodes = visibleTextNodes();
  const wrote = items.map((item) => {
    let node = nodes[item.index];
    if (!node || (node.getAttribute("name") || "") !== (item.name || "")) {
      node = nodes.find((candidate) => item.name && (candidate.getAttribute("name") || "") === item.name) || null;
    }
    if (!node) return { ok: false, label: item.label, expected: item.value };
    const result = writeNode(node, item);
    result.label = item.label;
    if (result.ok && item.path) node.setAttribute("data-apple-fill", String(item.path));
    return result;
  });
  watchFocus();
  return wrote;
}
"""


def _field(product: dict[str, Any], source: str) -> dict[str, Any] | None:
    parts = source.split(".")
    fields = product.get("fields") or {}
    if not isinstance(fields, dict) or len(parts) < 2 or parts[0] != "fields":
        return None
    if len(parts) == 2:
        item = fields.get(parts[1])
        return item if isinstance(item, dict) else None
    if len(parts) == 3 and parts[1] in {"specs", "attributes"}:
        for item in fields.get(parts[1]) or []:
            if isinstance(item, dict) and item.get("key") == parts[2]:
                return item
    return None


def _shown_sources(product: dict[str, Any], field: dict[str, Any]) -> list[dict[str, Any]]:
    names: dict[str, str] = {}
    for document in product.get("documents") or []:
        if not isinstance(document, dict):
            continue
        doc_id = str(document.get("docId") or "")
        if doc_id:
            names[doc_id] = str(document.get("filename") or doc_id)
    shown: list[dict[str, Any]] = []
    for source in field.get("sources") or []:
        if not isinstance(source, dict):
            continue
        quote = str(source.get("quote") or "").strip()
        if not quote:
            continue
        doc_id = str(source.get("docId") or "")
        item: dict[str, Any] = {
            "file": names.get(doc_id) or doc_id or "资料",
            "quote": quote[:1000],
        }
        page = source.get("page")
        if type(page) is int and page >= 1:
            item["page"] = page
        shown.append(item)
    return shown


def _skip_reason(field: dict[str, Any] | None) -> str:
    status = "missing" if field is None else str(field.get("status") or "missing")
    if status == "conflict":
        choices = "；".join(
            str(item.get("value") or "")
            for item in (field or {}).get("candidates") or []
            if isinstance(item, dict)
        )
        if choices:
            return f"资料互相矛盾（{choices}），需要你在页面上决定"
        return "资料互相矛盾，需要你在页面上决定"
    if status == "derived":
        return "这个值是推断的，需要你自己确认后再填"
    if status == "missing":
        return "资料里没有这个值，已留空"
    return "这个值没有可抄写的原文"


def plan_page_fills(product: dict[str, Any], controls: list[dict[str, Any]]) -> dict[str, Any]:
    """Plan values for the controls the caller can already see.

    Hidden fields on other pages are simply absent from ``controls``.
    """
    catalog = _catalog()
    used: set[str] = set()
    filled: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for index, control in enumerate(controls):
        if not isinstance(control, dict):
            continue
        kind = str(control.get("type") or "text").lower()
        if kind not in _TEXT_TYPES:
            continue
        found = _match_label(str(control.get("label") or ""), catalog)
        if found is None:
            continue
        source, shown = found
        if source in used:
            skipped.append({"label": shown, "reason": "这一页里这项已经有一格"})
            continue
        field = _field(product, source)
        status = "" if field is None else str(field.get("status") or "")
        value = "" if field is None else str(field.get("value") or "").strip()
        if status != "sourced" or not value:
            skipped.append({"label": shown, "reason": _skip_reason(field)})
            continue
        used.add(source)
        filled.append(
            {
                "index": index,
                "name": str(control.get("name") or ""),
                "label": shown,
                "value": value,
                "path": source,
                "sources": _shown_sources(product, field),
                "action": "select" if kind == "select" else "fill",
            }
        )
    return {"filled": filled, "skipped": skipped}


def fill_open_page(page: Any, product: dict[str, Any]) -> dict[str, Any]:
    """Write planned values into the page Playwright already has open.

    Does not navigate and does not click.
    """
    if page.evaluate("() => Boolean(window.__submitted)"):
        raise ValueError("页面显示已经提交，不再填写")
    controls = page.evaluate(LIST_JS)
    if not isinstance(controls, list):
        raise ValueError("这一页没有读到格子")
    if not controls:
        return {"filled": [], "skipped": [], "mismatches": [], "empty": True}
    plan = plan_page_fills(product, [item for item in controls if isinstance(item, dict)])
    skipped = list(plan["skipped"])
    if not plan["filled"]:
        return {"filled": [], "skipped": skipped, "mismatches": [], "empty": False}
    results = page.evaluate(
        APPLY_JS,
        [
            {
                "index": item["index"],
                "name": item["name"],
                "label": item["label"],
                "value": item["value"],
                "path": item["path"],
                "action": item["action"],
            }
            for item in plan["filled"]
        ],
    )
    if not isinstance(results, list):
        raise ValueError("这一页没有写成")
    filled: list[dict[str, str]] = []
    mismatches: list[str] = []
    for item, result in zip(plan["filled"], results, strict=False):
        if not isinstance(result, dict):
            mismatches.append(f"{item['label']}：页面读回的值不是「{item['value']}」")
            continue
        if result.get("reason") == "missing-option":
            skipped.append({"label": item["label"], "reason": f"下拉框里没有「{item['value']}」"})
            continue
        if not result.get("ok"):
            mismatches.append(f"{item['label']}：页面读回的值不是「{item['value']}」")
            continue
        filled.append(
            {
                "label": item["label"],
                "value": item["value"],
                "path": item["path"],
                "sources": item.get("sources") or [],
            }
        )
    if len(results) < len(plan["filled"]):
        for item in plan["filled"][len(results) :]:
            mismatches.append(f"{item['label']}：页面读回的值不是「{item['value']}」")
    return {"filled": filled, "skipped": skipped, "mismatches": mismatches, "empty": False}


class PageSession:
    """One headed browser the person logs into and pages through."""

    def __init__(self, *, headless: bool) -> None:
        self._headless = headless
        self._jobs: queue.Queue = queue.Queue()
        self._ready = threading.Event()
        self._start_error: BaseException | None = None
        self._playwright: Any = None
        self._browser: Any = None
        self._page: Any = None
        self._thread = threading.Thread(target=self._run, name="apple-fill-browser", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=30):
            raise RuntimeError("浏览器没有起来")
        if self._start_error is not None:
            raise RuntimeError(str(self._start_error)) from self._start_error

    def _run(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            self._start_error = RuntimeError("还没有安装 Playwright。请先运行：python -m playwright install chromium")
            self._ready.set()
            return
        try:
            manager = sync_playwright().start()
        except Exception as exc:
            self._start_error = exc
            self._ready.set()
            return
        self._playwright = manager
        self._ready.set()
        try:
            while True:
                job = self._jobs.get()
                if job is None:
                    break
                fn, reply = job
                try:
                    reply.put(fn())
                except Exception as exc:
                    reply.put(exc)
        finally:
            try:
                if self._browser is not None:
                    self._browser.close()
            finally:
                self._browser = None
                self._page = None
                manager.stop()

    def _call(self, fn: Any) -> Any:
        if self._start_error is not None:
            raise RuntimeError(str(self._start_error))
        if not self._thread.is_alive():
            raise RuntimeError("浏览器已经关掉")
        reply: queue.Queue = queue.Queue(1)
        self._jobs.put((fn, reply))
        try:
            result = reply.get(timeout=45)
        except queue.Empty as exc:
            raise RuntimeError("浏览器没有在时间内回应") from exc
        if isinstance(result, Exception):
            raise result
        return result

    def _ensure_page(self) -> Any:
        if self._browser is None:
            self._browser = self._playwright.chromium.launch(headless=self._headless)
        if self._page is None or self._page.is_closed():
            self._page = self._browser.new_page(viewport={"width": 1100, "height": 800})
            self._page.set_default_timeout(20_000)
        return self._page

    def open(self, url: str) -> str:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("这个网址打不开")

        def job() -> str:
            page = self._ensure_page()
            current = ""
            try:
                current = page.url or ""
            except Exception:
                current = ""
            if current.split("#")[0].rstrip("/") == url.split("#")[0].rstrip("/"):
                return str(page.url)
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=20_000)
            except Exception as exc:
                if exc.__class__.__name__ == "TimeoutError":
                    raise ValueError("这一页打开得太慢") from exc
                raise ValueError("这个网址没有打开") from exc
            return str(page.url)

        return str(self._call(job))

    def fill_current(self, product: dict[str, Any]) -> dict[str, Any]:
        def job() -> dict[str, Any]:
            if self._page is None or self._page.is_closed():
                raise ValueError("填写窗口已关闭，请再打开网址")
            return fill_open_page(self._page, product)

        result = self._call(job)
        if not isinstance(result, dict):
            raise RuntimeError("这一页没有写成")
        return result

    def focused_fill(self) -> str:
        """Return the filled field the person just clicked, then forget it."""

        def job() -> str:
            page = self._page
            if page is None or page.is_closed():
                return ""
            try:
                value = page.evaluate(
                    "() => { const value = window.__appleFillFocus || ''; window.__appleFillFocus = ''; return String(value); }"
                )
            except Exception:
                return ""
            return str(value or "")

        if not self._thread.is_alive() or self._start_error is not None:
            return ""
        try:
            result = self._call(job)
        except Exception:
            return ""
        return str(result or "")

    def evaluate(self, script: str) -> Any:
        def job() -> Any:
            if self._page is None or self._page.is_closed():
                raise ValueError("填写窗口已关闭，请再打开网址")
            return self._page.evaluate(script)

        return self._call(job)

    def close_window(self) -> None:
        def job() -> None:
            if self._browser is not None:
                self._browser.close()
            self._browser = None
            self._page = None

        if self._thread.is_alive() and self._start_error is None:
            self._call(job)

    def stop(self) -> None:
        if not self._thread.is_alive():
            return
        self._jobs.put(None)
        self._thread.join(timeout=10)


_session: PageSession | None = None
_session_lock = threading.Lock()


def current_desk_browser() -> PageSession | None:
    with _session_lock:
        session = _session
        if session is None or not session._thread.is_alive():
            return None
        return session


def desk_browser() -> PageSession:
    global _session
    with _session_lock:
        if _session is None or not _session._thread.is_alive():
            _session = PageSession(headless=False)
        return _session


def close_desk_browser() -> None:
    global _session
    with _session_lock:
        session = _session
    if session is not None:
        session.close_window()
