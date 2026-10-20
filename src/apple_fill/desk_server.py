"""Local-only desk server. The page and the API both stay on this machine."""

from __future__ import annotations

import json
import mimetypes
import webbrowser
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from apple_fill.desk_store import data_root, import_adapter, list_adapters, panel_summary, run_dir
from apple_fill.generate_adapter import controls_from_url, render_adapter
from apple_fill.llm_settings import public_llm, resolve_llm, save_llm

_MAX_BODY = 40 * 1024 * 1024


def ui_root() -> Path:
    candidates = [Path.cwd() / "ui", Path(__file__).resolve().parents[2] / "ui"]
    for candidate in candidates:
        if (candidate / "desk.html").is_file():
            return candidate
    raise FileNotFoundError("找不到 ui/desk.html")


def bundled_adapter_roots() -> list[Path]:
    return [Path.cwd() / "adapters", Path(__file__).resolve().parents[2] / "adapters"]


def _parts(body: bytes, content_type: str) -> list[tuple[str, str, bytes]]:
    header = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode()
    message = BytesParser(policy=default).parsebytes(header + body)
    found: list[tuple[str, str, bytes]] = []
    if not message.is_multipart():
        return found
    for part in message.iter_parts():
        disposition = part.get("Content-Disposition", "")
        if "form-data" not in disposition:
            continue
        name = part.get_param("name", header="content-disposition") or ""
        filename = part.get_filename() or ""
        payload = part.get_payload(decode=True) or b""
        found.append((str(name), str(filename), payload))
    return found


class DeskHandler(BaseHTTPRequestHandler):
    server_version = "AppleFillDesk/0.1"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length < 0 or length > _MAX_BODY:
            raise ValueError("请求太大")
        return self.rfile.read(length)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/adapters":
            briefs = list_adapters(data_root(), bundled_adapter_roots())
            public = [{key: item[key] for key in ("id", "version", "allowedHosts", "entryUrl", "source")} for item in briefs]
            self._json(200, {"adapters": public})
            return
        if path == "/api/llm":
            self._json(200, public_llm(data_root()))
            return
        if path in {"/", "/desk"}:
            self.send_response(302)
            self.send_header("Location", "/ui/desk.html")
            self.end_headers()
            return
        if path.startswith("/ui/"):
            self._file(path[len("/ui/") :])
            return
        self._json(404, {"error": "没有这个地址"})

    def _file(self, relative: str) -> None:
        root = ui_root().resolve()
        target = (root / unquote(relative)).resolve()
        if root not in target.parents and target != root:
            self._json(404, {"error": "没有这个文件"})
            return
        if not target.is_file():
            self._json(404, {"error": "没有这个文件"})
            return
        data = target.read_bytes()
        mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            body = self._read_body()
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
            return
        try:
            if path == "/api/adapters":
                brief = import_adapter(data_root(), body.decode("utf-8"))
                self._json(200, brief)
                return
            if path == "/api/adapters/generate":
                self._generate(body)
                return
            if path == "/api/extract":
                self._extract(body)
                return
            if path == "/api/llm":
                self._save_llm(body)
                return
        except Exception as exc:
            message = str(exc).strip() or "没有完成"
            self._json(400, {"error": message})
            return
        self._json(404, {"error": "没有这个地址"})

    def _generate(self, body: bytes) -> None:
        payload = json.loads(body.decode("utf-8"))
        controls = list(payload.get("controls") or [])
        page_url = str(payload.get("url") or "")
        if not controls:
            controls = controls_from_url(page_url)
        text, matched = render_adapter(page_url, controls)
        brief = import_adapter(data_root(), text)
        brief["matched"] = matched
        self._json(200, brief)

    def _save_llm(self, body: bytes) -> None:
        try:
            payload = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("设置没有读懂") from exc
        if not isinstance(payload, dict):
            raise ValueError("设置没有读懂")
        self._json(200, save_llm(data_root(), payload))

    def _extract(self, body: bytes) -> None:
        from apple_fill.extract import extract_product
        content_type = self.headers.get("Content-Type", "")
        parts = _parts(body, content_type)
        product_id = "未命名的一单"
        held: list[tuple[str, bytes]] = []
        for name, filename, payload in parts:
            if name == "productId":
                product_id = payload.decode("utf-8").strip() or product_id
                continue
            if name != "files" or not filename:
                continue
            safe = Path(filename).name
            if not safe or safe in {".", ".."}:
                continue
            held.append((safe, payload))
        if not held:
            self._json(400, {"error": "还没有文件"})
            return
        final = run_dir(data_root(), product_id)
        paths = []
        for safe, payload in held:
            target = final / safe
            target.write_bytes(payload)
            paths.append(target)
        picked = resolve_llm(data_root())
        if picked is None:
            self._json(400, {"error": "请先在设置里选择模型并保存"})
            return
        product = extract_product(paths, product_id, backend="llm", llm=picked)
        (final / "product.json").write_text(json.dumps(product, ensure_ascii=False, indent=2), encoding="utf-8")
        summary = panel_summary(product)
        relative = Path("runs") / final.name
        summary["run"] = str(relative)
        self._json(200, summary)


def serve_desk(port: int = 8780, *, open_browser: bool = True) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", port), DeskHandler)
    url = f"http://127.0.0.1:{port}/ui/desk.html"
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
