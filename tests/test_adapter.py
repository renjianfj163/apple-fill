from pathlib import Path

import pytest

from apple_fill.adapter import (
    Adapter,
    AdapterError,
    Step,
    assert_request_allowed,
    load_adapter,
    navigation_hosts,
)
from apple_fill.schema_io import SchemaError


def test_submit_is_not_an_adapter_action(tmp_path: Path):
    path = tmp_path / "adapter.yaml"
    path.write_text(
        """
id: bad
version: 1
entryUrl: http://127.0.0.1/
allowedHosts: ["127.0.0.1"]
steps:
  - id: send
    action: submit
    selector: "#submitBtn"
""",
        encoding="utf-8",
    )
    with pytest.raises(SchemaError):
        load_adapter(path)


def test_navigation_uses_platform_intersection(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("APPLE_FILL_ALLOWED_HOSTS", "certs.example")
    adapter = Adapter(
        id="cert",
        version=1,
        entry_url="https://certs.example/apply",
        allowed_hosts=frozenset({"certs.example", "other.example"}),
        resource_hosts=frozenset(),
        ignore_controls=frozenset(),
        session_ttl_seconds=None,
        steps=(Step(id="open", action="goto"),),
        path=Path("adapter.yaml"),
        sha256="b" * 64,
    )
    assert navigation_hosts(adapter) == frozenset({"certs.example"})
    monkeypatch.setenv("APPLE_FILL_ALLOWED_HOSTS", "blocked.example")
    with pytest.raises(AdapterError):
        navigation_hosts(adapter)


def test_cdn_can_load_without_becoming_a_page():
    navigate = frozenset({"certs.example"})
    resources = frozenset({"certs.example", "cdn.example"})
    assert_request_allowed(
        "https://cdn.example/app.js",
        navigation=False,
        navigate=navigate,
        resources=resources,
    )
    with pytest.raises(AdapterError):
        assert_request_allowed(
            "https://cdn.example/start",
            navigation=True,
            navigate=navigate,
            resources=resources,
        )
