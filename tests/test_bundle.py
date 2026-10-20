from bundle.build_bundle import asset_name, download_url, launcher


def test_python_asset_matches_the_pinned_release():
    name = asset_name("aarch64-apple-darwin")
    assert name == "cpython-3.11.17+20261013-aarch64-apple-darwin-install_only.tar.gz"
    assert name in download_url("aarch64-apple-darwin")


def test_windows_and_mac_launchers_use_the_bundled_browser():
    win_name, win = launcher("x86_64-pc-windows-msvc")
    mac_name, mac = launcher("aarch64-apple-darwin")
    assert win_name.endswith(".bat")
    assert mac_name.endswith(".command")
    assert b"PLAYWRIGHT_BROWSERS_PATH" in win
    assert b"PLAYWRIGHT_BROWSERS_PATH" in mac
    assert "apple_fill demo".encode() in win
    assert b"apple_fill demo" in mac
