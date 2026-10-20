import json
from pathlib import Path

import pytest

from apple_fill.extract import extract_product
from apple_fill.llm_settings import public_llm, resolve_llm, save_llm


def test_public_view_hides_the_key_and_keeps_it_for_extract(tmp_path: Path):
    secret = "sk-live-abcd"
    view = save_llm(
        tmp_path,
        {"mode": "preset", "preset": "deepseek", "model": "deepseek-chat", "apiKey": secret},
    )
    dumped = json.dumps(view)
    assert secret not in dumped
    assert view["hasKey"] is True
    assert view["preset"] == "deepseek"
    stored = json.loads((tmp_path / "llm.json").read_text(encoding="utf-8"))
    assert stored["keys"]["deepseek"] == secret
    url, model, api_key = resolve_llm(tmp_path)
    assert model == "deepseek-chat"
    assert url.endswith("/chat/completions")
    assert api_key == secret


def test_each_vendor_keeps_its_own_key(tmp_path: Path):
    save_llm(tmp_path, {"mode": "preset", "preset": "deepseek", "model": "deepseek-chat", "apiKey": "sk-deep-1111"})
    save_llm(tmp_path, {"mode": "preset", "preset": "qwen", "model": "qwen-plus", "apiKey": "sk-qwen-2222"})
    save_llm(tmp_path, {"mode": "preset", "preset": "deepseek", "model": "deepseek-chat", "apiKey": ""})
    _url, model, api_key = resolve_llm(tmp_path)
    assert model == "deepseek-chat"
    assert api_key == "sk-deep-1111"
    view = public_llm(tmp_path)
    assert "sk-deep-1111" not in json.dumps(view)
    assert "sk-qwen-2222" not in json.dumps(view)
    assert view["keySlots"] == ["deepseek", "qwen"]


def test_saved_labels_win_over_the_startup_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("APPLE_FILL_LLM_URL", "https://api.deepseek.com/chat/completions")
    monkeypatch.setenv("APPLE_FILL_LLM_MODEL", "deepseek-chat")
    monkeypatch.setenv("APPLE_FILL_LLM_API_KEY", "sk-from-env")
    assert resolve_llm(tmp_path)[1] == "deepseek-chat"
    save_llm(tmp_path, {"mode": "labels"})
    assert resolve_llm(tmp_path) is None
    view = public_llm(tmp_path)
    assert view["mode"] == "labels"
    assert view["fromEnv"] is False
    assert "sk-from-env" not in json.dumps(view)


def test_blank_key_keeps_the_startup_key_when_the_endpoint_matches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("APPLE_FILL_LLM_URL", "https://api.deepseek.com/chat/completions")
    monkeypatch.setenv("APPLE_FILL_LLM_MODEL", "deepseek-chat")
    monkeypatch.setenv("APPLE_FILL_LLM_API_KEY", "sk-from-env")
    save_llm(tmp_path, {"mode": "preset", "preset": "deepseek", "model": "deepseek-chat", "apiKey": ""})
    assert resolve_llm(tmp_path)[2] == "sk-from-env"


def test_custom_endpoint_rejects_a_non_http_address(tmp_path: Path):
    with pytest.raises(ValueError, match="http"):
        save_llm(tmp_path, {"mode": "custom", "url": "file:///secret", "model": "local", "apiKey": "x"})


def test_explicit_endpoint_is_what_extract_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, str] = {}

    def fake(paths, product_id, *, url, model, api_key):
        captured.update(url=url, model=model, api_key=api_key, product_id=product_id)
        return {"productId": product_id}

    import apple_fill.llm as llm

    monkeypatch.setattr(llm, "extract_with_llm", fake)
    monkeypatch.delenv("APPLE_FILL_LLM_URL", raising=False)
    extract_product([tmp_path / "missing.pdf"], "case", backend="llm", llm=("https://example.test/v1/chat/completions", "local", "sk-case"))
    assert captured == {
        "url": "https://example.test/v1/chat/completions",
        "model": "local",
        "api_key": "sk-case",
        "product_id": "case",
    }
