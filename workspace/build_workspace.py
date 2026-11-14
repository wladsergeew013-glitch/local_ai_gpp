"""Build a standalone Workspace folder, with pinned SDK/native runtime and rg.

python -m pip install -r workspace/requirements-desktop.txt
python workspace/build_workspace.py
"""

from __future__ import annotations
import argparse
import hashlib
import importlib.metadata
import json
import os
import subprocess
import sys
import uuid
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT / "workspace"


def manifest(directory):
    result = {}
    for path in sorted(directory.rglob("*")):
        if path.is_file() and path.name != "distribution.json":
            with path.open("rb") as stream:
                result[path.relative_to(directory).as_posix()] = hashlib.file_digest(
                    stream, "sha256"
                ).hexdigest()
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-frontend", action="store_true")
    args = parser.parse_args()
    if sys.platform != "win32":
        raise SystemExit(
            "Build the Windows EXE on Windows. Linux runs python -m workspace.launcher --headless."
        )
    for package, expected in [
        ("deepseek-harness-sdk", "0.2.2rc2"),
        ("deepseek-harness-runtime-bin", "0.2.2rc2"),
        ("pyinstaller", "6.22.3"),
        ("pywebview", "6.2.1"),
    ]:
        if importlib.metadata.version(package) != expected:
            raise SystemExit(
                "Install workspace/requirements-desktop.txt: "
                + package
                + " must be "
                + expected
            )
    if not args.skip_frontend:
        subprocess.run(["npm.cmd", "ci"], cwd=WORKSPACE / "frontend", check=True)
        subprocess.run(
            ["npm.cmd", "run", "build"], cwd=WORKSPACE / "frontend", check=True
        )
    if not (WORKSPACE / "frontend/dist/index.html").is_file():
        raise SystemExit("Workspace frontend is missing")
    staging = WORKSPACE / "build" / ("candidate-" + uuid.uuid4().hex)
    staging.mkdir(parents=True)
    output = staging / "out"
    version = (WORKSPACE / "VERSION").read_text().strip()
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--windowed",
        "--name",
        "LocalAIWorkspace",
        "--distpath",
        str(output),
        "--workpath",
        str(staging / "work"),
        "--specpath",
        str(staging),
        "--paths",
        str(ROOT),
        "--icon",
        str(ROOT / "models_storage/branding/icons/mini_agent_head_v2.ico"),
        "--add-data",
        str(WORKSPACE / "frontend/dist") + ";workspace_frontend",
        "--add-data",
        str(WORKSPACE / "VERSION") + ";workspace_version",
        "--collect-submodules",
        "workspace",
        "--exclude-module",
        "workspace.app.dsh_runner",
        "--exclude-module",
        "deepseek_harness",
        "--exclude-module",
        "deepseek_harness_runtime",
        "--collect-all",
        "webview",
        "--collect-submodules",
        "uvicorn",
        "--hidden-import",
        "webview.platforms.edgechromium",
        "--exclude-module",
        "llama_cpp",
        "--exclude-module",
        "backend",
        str(WORKSPACE / "launcher.py"),
    ]
    subprocess.run(command, cwd=ROOT, check=True)
    candidate = output / "LocalAIWorkspace"
    if not (candidate / "LocalAIWorkspace.exe").is_file():
        raise SystemExit("PyInstaller did not produce Workspace")
    agent = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--console",
        "--name",
        "LocalAIWorkspaceAgent",
        "--distpath",
        str(output),
        "--workpath",
        str(staging / "agent-work"),
        "--specpath",
        str(staging),
        "--paths",
        str(ROOT),
        "--collect-all",
        "deepseek_harness",
        "--collect-all",
        "deepseek_harness_runtime",
        "--copy-metadata",
        "deepseek-harness-sdk",
        "--copy-metadata",
        "deepseek-harness-runtime-bin",
        "--exclude-module",
        "llama_cpp",
        "--exclude-module",
        "webview",
        "--exclude-module",
        "backend",
        str(WORKSPACE / "app/dsh_runner.py"),
    ]
    subprocess.run(agent, cwd=ROOT, check=True)
    shutil.move(str(output / "LocalAIWorkspaceAgent"), str(candidate / "agent"))
    files = manifest(candidate)
    if not any(
        name.endswith("deepseek-harness-sdk-runtime-win-x64.exe") for name in files
    ):
        raise SystemExit("Pinned DSH executable missing")
    if not any(
        name.endswith("deepseek-harness-sdk-runtime-win-x64-rg.exe") for name in files
    ):
        raise SystemExit("Pinned DSH rg sidecar missing")
    (candidate / "distribution.json").write_text(
        json.dumps(
            {
                "product": "Local AI Workspace",
                "version": version,
                "schema_version": 1,
                "sdk_version": "0.2.2rc2",
                "dsh_format": 4,
                "files": files,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    destination = WORKSPACE / "dist"
    previous = WORKSPACE / ("build/previous-" + uuid.uuid4().hex)
    if destination.exists():
        destination.rename(previous)
    try:
        candidate.rename(destination)
    except BaseException:
        if previous.exists():
            previous.rename(destination)
        raise
    print(
        json.dumps(
            {
                "version": version,
                "dist": str(destination),
                "files": len(files),
                "previous": str(previous) if previous.exists() else None,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
