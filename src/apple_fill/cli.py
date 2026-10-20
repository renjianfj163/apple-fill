"""Command line for extracting product facts and filling up to submit."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from apple_fill.adapter import AdapterError, load_adapter
from apple_fill.demo_server import DemoServer
from apple_fill.documents import DocumentError
from apple_fill.extract import ExtractError, extract_product
from apple_fill.product import ProductError
from apple_fill.runner import FillResult, UnsafeAction, fill_form
from apple_fill.sample_data import write_samples
from apple_fill.schema_io import SchemaError


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        prog="apple-fill",
        description="从 PDF、Word、JPEG 抽出产品字段，并填写网页，停在提交按钮之前。",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    extract = commands.add_parser("extract", help="把资料抽成 product.json")
    extract.add_argument("--product-id", required=True)
    extract.add_argument("--out", type=Path, required=True)
    extract.add_argument("--backend", choices=("auto", "labels", "llm"), default="auto")
    extract.add_argument("files", nargs="+", type=Path)

    fill = commands.add_parser("fill", help="按适配包填写页面，不点击提交")
    fill.add_argument("--product", type=Path, required=True)
    fill.add_argument("--adapter", type=Path, required=True)
    fill.add_argument("--out-dir", type=Path, required=True)
    fill.add_argument("--base-url")
    fill.add_argument("--headed", action="store_true")
    fill.add_argument("--pause", action="store_true", help="填完后把浏览器留给操作人")

    demo = commands.add_parser("demo", help="用示例资料填写本地练习页")
    demo.add_argument("--headless", action="store_true")
    demo.add_argument("--product-id", default="demo-qp-200")

    args = parser.parse_args(argv)
    try:
        if args.command == "extract":
            return _extract(args)
        if args.command == "fill":
            return _fill(args)
        return _demo(args)
    except (ExtractError, ProductError, DocumentError, AdapterError, SchemaError, UnsafeAction) as exc:
        print(str(exc), file=sys.stderr)
        return 1


def _extract(args) -> int:
    product = extract_product(list(args.files), args.product_id, backend=args.backend)
    _write_json(args.out, product)
    print(f"已写入 {args.out}")
    return 0


def _fill(args) -> int:
    product = json.loads(args.product.read_text(encoding="utf-8"))
    adapter = load_adapter(args.adapter, base_url=args.base_url)
    result = fill_form(product, adapter, args.out_dir, headed=args.headed, pause=args.pause)
    _print_result(result)
    return 0


def _demo(args) -> int:
    root = Path.cwd()
    adapter_path = root / "adapters" / "demo-portal" / "adapter.yaml"
    if not adapter_path.is_file():
        raise AdapterError("请在项目根目录运行 demo，以便找到 adapters/demo-portal/adapter.yaml")
    samples = write_samples(root / "fixtures" / "sample")
    out_dir = root / "runs" / "demo"
    product = extract_product(samples, args.product_id, backend="labels")
    _write_json(out_dir / "product.json", product)
    server = DemoServer()
    base_url = server.start()
    try:
        adapter = load_adapter(adapter_path, base_url=base_url)
        result = fill_form(
            product,
            adapter,
            out_dir,
            headed=not args.headless,
            pause=not args.headless,
        )
    finally:
        server.stop()
    _print_result(result)
    return 0


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _print_result(result: FillResult) -> None:
    print(f"核对页：{result.handoff_path.parent / 'handoff.html'}")
    print("程序没有点击提交。")


if __name__ == "__main__":
    raise SystemExit(main())
