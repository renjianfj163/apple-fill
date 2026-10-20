from pathlib import Path

import pytest

from apple_fill.desk_store import import_adapter, list_adapters, match_host, panel_summary, read_brief
from apple_fill.schema_io import SchemaError


def test_import_rejects_submit(tmp_path: Path):
    text = """
id: bad
version: 1
entryUrl: https://certs.example/apply
allowedHosts: ["certs.example"]
steps:
  - id: send
    action: submit
    selector: "#submitBtn"
"""
    with pytest.raises(SchemaError):
        import_adapter(tmp_path, text)
    assert list(tmp_path.glob("adapters/*/*/adapter.yaml")) == []


def test_import_and_match_host(tmp_path: Path):
    text = """
id: certs
version: 2
entryUrl: https://certs.example/apply
allowedHosts:
  - certs.example
steps:
  - id: open
    action: goto
    url: https://certs.example/apply
  - id: wait
    action: wait_for
    selector: "#submitBtn"
"""
    brief = import_adapter(tmp_path, text)
    assert brief["id"] == "certs"
    assert brief["source"] == "user"
    found = match_host("certs.example", list_adapters(tmp_path, []))
    assert found is not None
    assert found["version"] == 2
    assert match_host("other.example", list_adapters(tmp_path, [])) is None


def test_bundled_brief_reads_hosts(tmp_path: Path):
    folder = tmp_path / "demo-portal"
    folder.mkdir()
    (folder / "adapter.yaml").write_text(
        "id: demo-portal\nversion: 1\nentryUrl: http://127.0.0.1/\nallowedHosts:\n  - 127.0.0.1\n",
        encoding="utf-8",
    )
    brief = read_brief(folder / "adapter.yaml", "bundled")
    assert brief is not None
    assert brief["allowedHosts"] == ["127.0.0.1"]


def test_panel_summary_counts_sourced_and_blanks():
    product = {
        "fields": {
            "productName": {"value": "台灯", "status": "sourced", "sources": []},
            "modelNo": {"value": None, "status": "conflict", "sources": []},
            "brand": {"value": None, "status": "missing", "sources": []},
            "specs": [
                {"key": "ratedVoltage", "label": "额定电压", "value": None, "status": "missing", "sources": []},
            ],
        }
    }
    summary = panel_summary(product)
    assert summary["sourced"] == 1
    reasons = {item["field"]: item["reason"] for item in summary["blanks"]}
    assert reasons["型号"] == "两份文件不一致"
    assert reasons["品牌"] == "资料里没有"
    assert reasons["额定电压"] == "资料里没有"
