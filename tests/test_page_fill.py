from pathlib import Path

import pytest

from apple_fill.demo_server import DemoServer
from apple_fill.extract import extract_product
from apple_fill.page_fill import PageSession, plan_page_fills
from apple_fill.sample_data import write_docx, write_jpeg


def _product() -> dict:
    return {
        "fields": {
            "productName": {"value": "青苹空气净化器", "status": "sourced", "sources": []},
            "modelNo": {"value": None, "status": "missing", "sources": []},
            "productNameEn": {
                "value": "Qingping",
                "status": "derived",
                "sources": [],
            },
            "specs": [
                {
                    "key": "ratedVoltage",
                    "label": "额定电压",
                    "value": "220V",
                    "status": "sourced",
                    "sources": [],
                }
            ],
        }
    }


def test_plan_uses_only_the_controls_on_this_page():
    planned = plan_page_fills(
        _product(),
        [
            {"name": "productName", "label": "产品名称：", "type": "text"},
            {"name": "modelNo", "label": "型号", "type": "text"},
            {"name": "productNameEn", "label": "英文名称", "type": "text"},
            {"name": "volte", "label": "是否支持VoLTE", "type": "checkbox"},
            {"name": "again", "label": "产品名称", "type": "text"},
        ],
    )
    assert [item["name"] for item in planned["filled"]] == ["productName"]
    assert planned["filled"][0]["value"] == "青苹空气净化器"
    reasons = {item["label"]: item["reason"] for item in planned["skipped"]}
    assert "资料里没有" in reasons["型号"]
    assert "推断" in reasons["英文名称"]
    assert reasons["产品名称"] == "这一页里这项已经有一格"
    assert "额定电压" not in {item["label"] for item in planned["filled"]}


@pytest.fixture
def practice_form(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("APPLE_FILL_ALLOWED_HOSTS", raising=False)
    monkeypatch.setattr("apple_fill.documents.ocr_image", lambda path: "")
    server = DemoServer()
    base_url = server.start()
    try:
        yield base_url
    finally:
        server.stop()


def _extracted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    monkeypatch.setattr("apple_fill.documents.ocr_image", lambda path: "")
    docx_path = tmp_path / "spec.docx"
    image_path = tmp_path / "label.jpg"
    write_docx(docx_path)
    write_jpeg(image_path, None)
    return extract_product([docx_path, image_path], "qp-200", backend="labels")


def test_each_click_fills_only_the_visible_page(tmp_path: Path, practice_form: str, monkeypatch: pytest.MonkeyPatch):
    product = _extracted(tmp_path, monkeypatch)
    session = PageSession(headless=True)
    try:
        session.open(practice_form + "/")
        first = session.fill_current(product)
        labels = {item["label"] for item in first["filled"]}
        assert "产品名称" in labels
        assert "额定电压" not in labels
        assert "产品描述" not in labels
        assert first["mismatches"] == []
        visible = session.evaluate(
            """() => ({
              productName: document.getElementById("productName").value,
              description: document.getElementById("description").value,
              ratedVoltage: document.getElementById("ratedVoltage").value,
              nextHidden: document.getElementById("nextBtn").hidden,
              submitted: Boolean(window.__submitted),
              agreed: document.getElementById("agreeTerms").checked
            })"""
        )
        assert visible["productName"] == "青苹空气净化器"
        assert visible["description"] == ""
        assert visible["ratedVoltage"] == ""
        assert visible["nextHidden"] is False
        assert visible["submitted"] is False
        assert visible["agreed"] is False

        session.evaluate("() => { document.getElementById('nextBtn').click(); return true; }")
        second = session.fill_current(product)
        again = {item["label"] for item in second["filled"]}
        assert "产品描述" in again
        assert "额定电压" in again
        assert second["mismatches"] == []
        after = session.evaluate(
            """() => ({
              productName: document.getElementById("productName").value,
              description: document.getElementById("description").value,
              ratedVoltage: document.getElementById("ratedVoltage").value,
              submitted: Boolean(window.__submitted)
            })"""
        )
        assert after["productName"] == "青苹空气净化器"
        assert after["description"].startswith("用于室内")
        assert after["ratedVoltage"] == "220V"
        assert after["submitted"] is False
    finally:
        session.stop()
