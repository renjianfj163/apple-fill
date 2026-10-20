import json
from pathlib import Path

import pytest

from apple_fill.adapter import load_adapter
from apple_fill.demo_server import DemoServer
from apple_fill.extract import extract_product
from apple_fill.runner import UnsafeAction, fill_form
from apple_fill.sample_data import write_docx, write_jpeg


ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "adapters" / "demo-portal" / "adapter.yaml"


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


def _product(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    monkeypatch.setattr("apple_fill.documents.ocr_image", lambda path: "")
    docx_path = tmp_path / "spec.docx"
    image_path = tmp_path / "label.jpg"
    write_docx(docx_path)
    write_jpeg(image_path, None)
    return extract_product([docx_path, image_path], "qp-200", backend="labels")


def test_fill_stops_before_submit(tmp_path: Path, practice_form: str, monkeypatch: pytest.MonkeyPatch):
    product = _product(tmp_path, monkeypatch)
    adapter = load_adapter(ADAPTER, base_url=practice_form)
    result = fill_form(product, adapter, tmp_path / "run", headed=False, pause=False)
    handoff = json.loads(result.handoff_path.read_text(encoding="utf-8"))

    assert result.clicked_submit is False
    assert result.page_submitted is False
    assert handoff["stoppedBeforeSubmit"] is True
    assert handoff["pageSubmitted"] is False
    assert result.mismatches == []
    assert "click:next" in result.actions
    assert "wait:stop-before-submit" in result.actions
    assert [action for action in result.actions if action.startswith("click:")] == ["click:next"]

    controls = {item["name"]: item for item in handoff["readback"]["controls"]}
    assert controls["productName"]["value"] == "青苹空气净化器"
    assert controls["ratedVoltage"]["value"] == "220V"
    assert controls["description"]["value"].startswith("用于室内")
    assert controls["productNameEn"]["value"] == ""
    assert controls["agreeTerms"]["checked"] is False
    assert controls["promoOptin"]["checked"] is True
    assert controls["promoOptin"]["origin"] == "unknown"
    assert controls["_csrf"]["origin"] == "ignored"
    assert any(item["name"] == "promoOptin" for item in handoff["needsOperatorReview"])
    assert any(item["path"] == "fields.productNameEn" for item in handoff["skipped"])
    assert any(item["path"] == "fields.category" for item in handoff["skipped"])
    assert (tmp_path / "run" / "handoff.html").is_file()
    assert (tmp_path / "run" / "page.png").is_file()


def test_clicking_submit_is_refused(tmp_path: Path, practice_form: str, monkeypatch: pytest.MonkeyPatch):
    product = _product(tmp_path, monkeypatch)
    source = ADAPTER.read_text(encoding="utf-8")
    bad = tmp_path / "adapter.yaml"
    bad.write_text(
        source.replace(
            """  - id: stop-before-submit
    action: wait_for
    selector: "#submitBtn"
""",
            """  - id: send
    action: click
    selector: "#submitBtn"
""",
        ),
        encoding="utf-8",
    )
    adapter = load_adapter(bad, base_url=practice_form)
    with pytest.raises(UnsafeAction, match="提交"):
        fill_form(product, adapter, tmp_path / "run", headed=False, pause=False)
