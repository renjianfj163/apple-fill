"""Build a Windows or Mac folder that carries its own Python and browser.

The customer unpacks one zip and starts the launcher. No separate Python
install is required. Chromium is already inside the zip.
"""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

RELEASE = "20261013"
PYTHON = "3.11.17"
ASSET = "cpython-{python}+{release}-{triple}-install_only.tar.gz"

BUNDLE_NAMES = {
    "x86_64-pc-windows-msvc": "AppleFill-Windows-x64.zip",
    "aarch64-apple-darwin": "AppleFill-Mac-AppleSilicon.zip",
}


def asset_name(triple: str) -> str:
    if triple not in BUNDLE_NAMES:
        raise ValueError(f"还没有这个系统的安装包：{triple}")
    return ASSET.format(python=PYTHON, release=RELEASE, triple=triple)


def download_url(triple: str) -> str:
    return (
        "https://github.com/astral-sh/python-build-standalone/releases/download/"
        f"{RELEASE}/{asset_name(triple)}"
    )


def python_executable(python_dir: Path) -> Path:
    windows = python_dir / "python.exe"
    if windows.is_file():
        return windows
    unix = python_dir / "bin" / "python3"
    if unix.is_file():
        return unix
    raise FileNotFoundError(f"解压后的 Python 里没有解释器：{python_dir}")


def launcher(triple: str) -> tuple[str, bytes]:
    if triple.endswith("windows-msvc"):
        text = """@echo off
chcp 65001 >nul
setlocal
set "ROOT=%~dp0"
set "PLAYWRIGHT_BROWSERS_PATH=%ROOT%browsers"
set PYTHONUTF8=1
set "PY=%ROOT%python\\python.exe"
cd /d "%ROOT%app"
if "%~1"=="" (
  echo Apple Fill 在这台电脑上运行。资料不会上传给软件提供者。
  echo 使用前请设置你自己的模型：
  echo   set APPLE_FILL_LLM_URL=你的接口
  echo   set APPLE_FILL_LLM_MODEL=你的模型
  echo   set APPLE_FILL_LLM_API_KEY=你的密钥
  "%PY%" -m apple_fill --help
  echo.
  echo 练习可运行： "%PY%" -m apple_fill demo
  pause
) else (
  "%PY%" -m apple_fill %*
)
"""
        return "启动AppleFill.bat", text.replace("\n", "\r\n").encode("utf-8")
    text = """#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
export PLAYWRIGHT_BROWSERS_PATH="$ROOT/browsers"
export PYTHONUTF8=1
PY="$ROOT/python/bin/python3"
cd "$ROOT/app"
if [ "$#" -eq 0 ]; then
  echo "Apple Fill 在这台电脑上运行。资料不会上传给软件提供者。"
  echo "使用前请设置你自己的模型："
  echo "  export APPLE_FILL_LLM_URL=你的接口"
  echo "  export APPLE_FILL_LLM_MODEL=你的模型"
  echo "  export APPLE_FILL_LLM_API_KEY=你的密钥"
  "$PY" -m apple_fill --help
  echo
  echo "练习可运行： $PY -m apple_fill demo"
  read -r -p "按回车关闭..." _
else
  "$PY" -m apple_fill "$@"
fi
"""
    return "启动AppleFill.command", text.encode("utf-8")


def copy_app(repo: Path, app: Path) -> None:
    if app.exists():
        shutil.rmtree(app)
    app.mkdir(parents=True)
    shutil.copy2(repo / "pyproject.toml", app / "pyproject.toml")
    shutil.copytree(
        repo / "src",
        app / "src",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.egg-info"),
    )
    shutil.copytree(repo / "adapters", app / "adapters")


def fetch_python(triple: str, folder: Path) -> None:
    url = download_url(triple)
    archive_path = folder / "python.tar.gz"
    request = urllib.request.Request(url, headers={"User-Agent": "apple-fill-bundle"})
    with urllib.request.urlopen(request, timeout=180) as response, archive_path.open("wb") as handle:
        shutil.copyfileobj(response, handle)
    with tarfile.open(archive_path, "r:gz") as archive:
        kwargs = {"filter": "data"} if sys.version_info >= (3, 12) else {}
        archive.extractall(folder, **kwargs)
    archive_path.unlink()


def install_app(python: Path, app: Path, browsers: Path) -> None:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers)
    subprocess.run(
        [str(python), "-m", "pip", "install", "--disable-pip-version-check", str(app)],
        check=True,
        env=env,
    )
    browsers.mkdir(parents=True, exist_ok=True)
    subprocess.run([str(python), "-m", "playwright", "install", "chromium"], check=True, env=env)


def write_zip(folder: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(folder.rglob("*")):
            if not path.is_file():
                continue
            info = zipfile.ZipInfo.from_file(path, arcname=str(Path("AppleFill") / path.relative_to(folder)))
            info.flag_bits |= 0x800
            if path.suffix == ".command":
                info.external_attr = (stat.S_IFREG | 0o755) << 16
            with path.open("rb") as handle:
                archive.writestr(info, handle.read())


def build(repo: Path, triple: str, output: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="apple-fill-bundle-") as temporary:
        folder = Path(temporary) / "AppleFill"
        folder.mkdir()
        fetch_python(triple, folder)
        app = folder / "app"
        copy_app(repo, app)
        install_app(python_executable(folder / "python"), app, folder / "browsers")
        name, body = launcher(triple)
        launcher_path = folder / name
        launcher_path.write_bytes(body)
        if name.endswith(".command"):
            launcher_path.chmod(launcher_path.stat().st_mode | stat.S_IEXEC)
        write_zip(folder, output)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="打一个带 Python 和浏览器的安装包")
    parser.add_argument("--triple", required=True, choices=tuple(BUNDLE_NAMES))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    build(args.repo, args.triple, args.output)
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
