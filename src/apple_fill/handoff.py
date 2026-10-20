"""Operator handoff: what was filled, where it came from, and what was left alone."""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any


def write_handoff(directory: Path, payload: dict[str, Any]) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / "handoff.json"
    html_path = directory / "handoff.html"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    html_path.write_text(render_html(payload), encoding="utf-8")
    return json_path, html_path


def render_html(payload: dict[str, Any]) -> str:
    filled = "".join(_field_block(item) for item in payload.get("filled") or []) or "<p>没有写入任何字段。</p>"
    skipped = "".join(_skip_block(item) for item in payload.get("skipped") or []) or "<p>没有跳过的字段。</p>"
    review = payload.get("needsOperatorReview") or []
    review_html = "".join(
        f"<li><code>{html.escape(item.get('name') or '(无 name)')}</code> "
        f"{html.escape(_control_text(item))}</li>"
        for item in review
    ) or "<li>没有发现未声明却已有值的控件。</li>"
    mismatches = "".join(
        f"<li>{html.escape(item)}</li>" for item in payload.get("mismatches") or []
    ) or "<li>读回值与要填的值一致。</li>"
    shot = payload.get("screenshot")
    image = f'<img src="{html.escape(shot)}" alt="填写后的页面"/>' if shot else ""
    ttl = payload.get("sessionTtlSeconds")
    ttl_line = f"<p>站点会话大约 {int(ttl)} 秒。请在页面过期前核对并提交。</p>" if ttl else ""
    missing_text = payload.get("documentsWithoutText") or []
    missing_line = ""
    if missing_text:
        names = "、".join(html.escape(str(name)) for name in missing_text)
        missing_line = (
            f"<p>这些文件没有抽出文字：{names}。图片和扫描件需要本机的 Tesseract（chi_sim）。"
            "字段只来自已经读出文字的 PDF 和 Word。</p>"
        )
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8"/>
  <title>请核对后自行提交</title>
  <style>
    body {{ margin: 0; font-family: "Segoe UI", "Microsoft YaHei", sans-serif; color: #1f2933; background: #f6f4ef; }}
    main {{ max-width: 980px; margin: 0 auto; padding: 32px 20px 64px; }}
    h1 {{ font-size: 28px; margin-bottom: 8px; }}
    .banner {{ background: #fff; border-left: 6px solid #0f766e; padding: 16px 18px; }}
    section {{ margin-top: 28px; }}
    article {{ background: #fff; padding: 14px 16px; margin: 10px 0; }}
    .quote {{ color: #52606d; font-size: 14px; }}
    .warn {{ background: #fff6f0; border-left: 6px solid #c2410c; padding: 12px 14px; }}
    img {{ width: 100%; margin-top: 12px; background: #fff; }}
    code {{ font-family: Consolas, monospace; }}
  </style>
</head>
<body>
<main>
  <div class="banner">
    <h1>表单已填完，提交留给你</h1>
    <p>程序只填写了下面这些有原文出处的字段，没有点击提交，也没有勾选「本人保证属实」。</p>
    {ttl_line}
    {missing_line}
  </div>
  <section>
    <h2>已写入</h2>
    {filled}
  </section>
  <section>
    <h2>没有代填</h2>
    {skipped}
  </section>
  <section class="warn">
    <h2>页面上多出来的值</h2>
    <ul>{review_html}</ul>
  </section>
  <section>
    <h2>读回核对</h2>
    <ul>{mismatches}</ul>
  </section>
  <section>
    <h2>填写后的页面</h2>
    {image}
  </section>
</main>
</body>
</html>
"""


def _field_block(item: dict[str, Any]) -> str:
    quotes = "".join(
        f"<div class=\"quote\">{html.escape(source.get('docId', ''))} 第 {html.escape(str(source.get('page', '')))} 页：{html.escape(source.get('quote', ''))}</div>"
        for source in item.get("sources") or []
    )
    return (
        f"<article><strong>{html.escape(str(item.get('label') or item.get('path')))}</strong>"
        f"<div>{html.escape(str(item.get('value') or ''))}</div>{quotes}</article>"
    )


def _skip_block(item: dict[str, Any]) -> str:
    return (
        f"<article><strong>{html.escape(str(item.get('path') or ''))}</strong>"
        f"<div>{html.escape(str(item.get('reason') or ''))}</div></article>"
    )


def _control_text(item: dict[str, Any]) -> str:
    if item.get("type") in {"checkbox", "radio"}:
        return "已勾选" if item.get("checked") else "未勾选"
    return str(item.get("value") or "")
