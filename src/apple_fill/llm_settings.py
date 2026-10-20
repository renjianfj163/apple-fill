"""Desk choices for which model extracts a case.

The key stays in llm.json on this computer. Public replies never include it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

PRESETS: tuple[dict[str, str], ...] = (
    {
        "id": "deepseek",
        "label": "DeepSeek",
        "url": "https://api.deepseek.com/chat/completions",
        "model": "deepseek-chat",
    },
    {
        "id": "qwen",
        "label": "通义千问",
        "url": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        "model": "qwen-plus",
    },
    {
        "id": "glm",
        "label": "智谱 GLM",
        "url": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
        "model": "glm-4-flash",
    },
    {
        "id": "kimi",
        "label": "Kimi",
        "url": "https://api.moonshot.cn/v1/chat/completions",
        "model": "moonshot-v1-8k",
    },
    {
        "id": "openai",
        "label": "OpenAI",
        "url": "https://api.openai.com/v1/chat/completions",
        "model": "gpt-4o-mini",
    },
)

_PRESET_BY_ID = {item["id"]: item for item in PRESETS}
_MODES = {"labels", "preset", "custom"}


def _settings_path(root: Path) -> Path:
    return root / "llm.json"


def _env_endpoint() -> tuple[str, str, str]:
    return (
        os.environ.get("APPLE_FILL_LLM_URL", "").strip(),
        os.environ.get("APPLE_FILL_LLM_MODEL", "").strip(),
        os.environ.get("APPLE_FILL_LLM_API_KEY", "").strip(),
    )


def _read(root: Path) -> dict[str, Any] | None:
    path = _settings_path(root)
    if not path.is_file():
        return None
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def _models(saved: dict[str, Any] | None) -> dict[str, str]:
    raw = (saved or {}).get("models") or {}
    if not isinstance(raw, dict):
        return {}
    found: dict[str, str] = {}
    for key, value in raw.items():
        if isinstance(key, str) and isinstance(value, str) and value.strip():
            found[key] = value.strip()
    return found


def _keys(saved: dict[str, Any] | None) -> dict[str, str]:
    raw = (saved or {}).get("keys") or {}
    if not isinstance(raw, dict):
        return {}
    found: dict[str, str] = {}
    for key, value in raw.items():
        if isinstance(key, str) and isinstance(value, str) and value.strip():
            found[key] = value.strip()
    return found


def _match_preset(url: str) -> str:
    for item in PRESETS:
        if item["url"] == url:
            return item["id"]
    return ""


def _check_url(url: str) -> str:
    cleaned = url.strip()
    parsed = urlparse(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("接口地址需要以 http:// 或 https:// 开头")
    if len(cleaned) > 500:
        raise ValueError("接口地址太长")
    return cleaned


def _check_model(model: str) -> str:
    cleaned = model.strip()
    if not cleaned or any(ch in cleaned for ch in "\r\n\t"):
        raise ValueError("请填写模型名")
    if len(cleaned) > 128:
        raise ValueError("模型名太长")
    return cleaned


def _check_key(api_key: str) -> str:
    cleaned = api_key.strip()
    if any(ch in cleaned for ch in "\r\n"):
        raise ValueError("密钥里不能换行")
    if len(cleaned) > 512:
        raise ValueError("密钥太长")
    return cleaned


def _slot(mode: str, preset: str) -> str:
    return "custom" if mode == "custom" else preset


def _endpoint_of(saved: dict[str, Any]) -> tuple[str, str, str] | None:
    mode = saved.get("mode")
    if mode not in _MODES or mode == "labels":
        return None
    models = _models(saved)
    keys = _keys(saved)
    if mode == "custom":
        url = str(saved.get("url") or "").strip()
        model = models.get("custom") or str(saved.get("model") or "").strip()
        if not url or not model:
            return None
        return url, model, keys.get("custom", "")
    preset_id = str(saved.get("preset") or "")
    preset = _PRESET_BY_ID.get(preset_id)
    if preset is None:
        return None
    model = models.get(preset_id) or preset["model"]
    return preset["url"], model, keys.get(preset_id, "")


def resolve_llm(root: Path) -> tuple[str, str, str] | None:
    """Return url, model, api key. None means the desk has no model yet."""
    saved = _read(root)
    if saved is not None:
        return _endpoint_of(saved)
    url, model, api_key = _env_endpoint()
    if url and model:
        return url, model, api_key
    return None


def public_llm(root: Path) -> dict[str, Any]:
    saved = _read(root)
    models = _models(saved)
    keys = _keys(saved)
    env_url, env_model, env_key = _env_endpoint()
    from_env = saved is None and bool(env_url and env_model)
    if saved is None:
        mode = "env" if from_env else "labels"
        preset = _match_preset(env_url) if from_env else ""
        model = env_model if from_env else ""
        custom_url = "" if preset or not from_env else env_url
        if from_env and not preset:
            mode = "env"
        has_key = from_env and bool(env_key)
        active_slot = ""
    else:
        mode = str(saved.get("mode") or "labels")
        if mode not in _MODES:
            mode = "labels"
        preset = str(saved.get("preset") or "") if mode == "preset" else ""
        if mode == "preset" and preset not in _PRESET_BY_ID:
            mode = "labels"
            preset = ""
        custom_url = str(saved.get("url") or "") if isinstance(saved.get("url"), str) else ""
        active_slot = _slot(mode, preset) if mode in {"preset", "custom"} else ""
        model = models.get(active_slot, "")
        if mode == "preset" and not model and preset in _PRESET_BY_ID:
            model = _PRESET_BY_ID[preset]["model"]
        has_key = bool(keys.get(active_slot))
    return {
        "mode": mode,
        "preset": preset if mode == "preset" or (from_env and preset) else "",
        "model": model,
        "customUrl": custom_url,
        "hasKey": has_key,
        "keySlots": sorted(keys),
        "models": models,
        "fromEnv": from_env,
        "presets": [dict(item) for item in PRESETS],
    }


def _write(root: Path, payload: dict[str, Any]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    path = _settings_path(root)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def save_llm(root: Path, incoming: dict[str, Any]) -> dict[str, Any]:
    mode = str(incoming.get("mode") or "")
    if mode == "labels":
        raise ValueError("请选择模型")
    if mode not in _MODES:
        raise ValueError("请选择模型")
    previous = _read(root) or {}
    models = _models(previous)
    keys = _keys(previous)
    preset = str(incoming.get("preset") or "")
    url = str(previous.get("url") or "") if isinstance(previous.get("url"), str) else ""
    if mode == "preset":
        if preset not in _PRESET_BY_ID:
            raise ValueError("没有这个模型")
        catalog = _PRESET_BY_ID[preset]
        typed = incoming.get("model")
        model = _check_model(str(typed if typed not in (None, "") else catalog["model"]))
        models[preset] = model
        endpoint = catalog["url"]
        slot = preset
    else:
        url = _check_url(str(incoming.get("url") or ""))
        model = _check_model(str(incoming.get("model") or ""))
        models["custom"] = model
        endpoint = url
        slot = "custom"
        preset = ""
    typed_key = incoming.get("apiKey")
    if typed_key not in (None, ""):
        cleaned = _check_key(str(typed_key))
        if cleaned:
            keys[slot] = cleaned
    elif slot not in keys:
        env_url, env_model, env_key = _env_endpoint()
        if env_key and env_url == endpoint and env_model == model:
            keys[slot] = env_key
    stored = {
        "mode": mode,
        "preset": preset,
        "url": url,
        "models": models,
        "keys": keys,
    }
    _write(root, stored)
    return public_llm(root)
