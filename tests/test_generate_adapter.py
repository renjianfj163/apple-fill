from apple_fill.adapter import load_adapter
from apple_fill.desk_store import import_adapter
from apple_fill.generate_adapter import GenerateError, controls_from_html, render_adapter


def test_render_matches_known_labels_and_skips_the_rest(tmp_path):
    text, matched = render_adapter(
        "https://certs.example/apply",
        [
            {"name": "productName", "label": "产品名称：", "type": "text"},
            {"name": "modelNo", "label": "规格型号", "type": "text"},
            {"name": "volte", "label": "是否支持VoLTE", "type": "checkbox"},
            {"name": "备注", "label": "产品名称", "type": "text"},
        ],
    )
    assert matched == ["产品名称", "型号"]
    brief = import_adapter(tmp_path, text)
    loaded = load_adapter(tmp_path / "adapters" / brief["id"] / "v1" / "adapter.yaml")
    assert loaded.allowed_hosts == frozenset({"certs.example"})
    filled = [step.name for step in loaded.steps if step.action == "fill"]
    assert filled == ["productName", "modelNo"]


def test_html_uses_the_row_label_for_a_text_field():
    controls = controls_from_html(
        """
        <div class="row">
          <div class="label">产品特性描述：</div>
          <div class="field"><textarea name="featureDesc"></textarea></div>
        </div>
        <div class="row">
          <div class="label">语音模式：</div>
          <label class="opt"><input type="checkbox" name="volte"/>是否支持VoLTE</label>
        </div>
        """
    )
    by_name = {item["name"]: item for item in controls}
    assert by_name["featureDesc"]["label"] == "产品特性描述："
    assert by_name["volte"]["label"] == "是否支持VoLTE"
    named = controls_from_html(
        '<div class="label">内置生成式</div><span>模型名称：</span><input type="text" name="aiModelName"/>'
    )
    assert named[0]["label"] == "模型名称："


def test_render_refuses_a_page_with_no_known_fields():
    try:
        render_adapter(
            "https://certs.example/apply",
            [{"name": "volte", "label": "是否支持VoLTE", "type": "checkbox"}],
        )
    except GenerateError as exc:
        assert "没有对上" in str(exc)
    else:
        raise AssertionError("expected GenerateError")
