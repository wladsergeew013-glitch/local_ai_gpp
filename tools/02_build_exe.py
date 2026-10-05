from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
OUT = TOOLS / "out"
LOG = OUT / "build_exe.log"
DOWNLOADS = OUT / "downloads"
PY_EMBED_VERSION = os.environ.get("LOCAL_AI_GPP_EMBED_PYTHON_VERSION", "3.12.10")
PY_EMBED_ZIP = f"python-{PY_EMBED_VERSION}-embed-amd64.zip"
PY_EMBED_URL = f"https://www.python.org/ftp/python/{PY_EMBED_VERSION}/{PY_EMBED_ZIP}"
GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"
CPU_VERSION = os.environ.get("LLAMA_CPP_CPU_VERSION", "0.3.36")
CUDA_VERSION = os.environ.get("LLAMA_CPP_CUDA_VERSION", "0.3.36")
MODEL_EXTENSIONS = {".gguf", ".bin", ".safetensors"}
PARENT_REDIRECTS_BUILD_LOG = os.environ.get("LOCAL_AI_GPP_BUILD_LOG_REDIRECTED") == "1"


def app_version() -> str:
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    parts = version.split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        raise SystemExit("[ERROR] VERSION must contain MAJOR.MINOR.PATCH")
    package = json.loads((ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))
    package_lock = json.loads((ROOT / "frontend" / "package-lock.json").read_text(encoding="utf-8"))
    if package.get("version") != version or package_lock.get("version") != version:
        raise SystemExit("[ERROR] Frontend package versions must match VERSION")
    return version


def write_windows_version_file() -> Path:
    version = app_version()
    major, minor, patch = map(int, version.split("."))
    version_file = OUT / "LocalAIGPP_version_info.txt"
    version_file.parent.mkdir(parents=True, exist_ok=True)
    version_file.write_text(
        "VSVersionInfo(\n"
        f"  ffi=FixedFileInfo(filevers=({major}, {minor}, {patch}, 0), prodvers=({major}, {minor}, {patch}, 0), "
        "mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),\n"
        "  kids=[StringFileInfo([StringTable('040904B0', [\n"
        "    StringStruct('CompanyName', 'Local AI GPP'),\n"
        "    StringStruct('FileDescription', 'Local AI GPP desktop application'),\n"
        f"    StringStruct('FileVersion', '{version}'),\n"
        "    StringStruct('InternalName', 'LocalAIGPP'),\n"
        "    StringStruct('OriginalFilename', 'LocalAIGPP.exe'),\n"
        "    StringStruct('ProductName', 'Local AI GPP'),\n"
        f"    StringStruct('ProductVersion', '{version}')\n"
        "  ])]), VarFileInfo([VarStruct('Translation', [1033, 1200])])]\n"
        ")\n",
        encoding="utf-8",
    )
    return version_file


def _write_log_file_line(message: str) -> None:
    """Append to the build log when this Python process owns the log file.

    tools\02_build_exe.bat redirects this script stdout/stderr into the same
    build_exe.log. On Windows that redirected handle can lock the file, so direct
    open(..., 'a'/'w') from Python raises PermissionError. In that mode stdout is
    already the log, therefore direct file writes are intentionally skipped.
    """
    if PARENT_REDIRECTS_BUILD_LOG:
        return
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8", errors="replace") as handle:
            handle.write(message + "\n")
    except OSError:
        # Do not fail the build because logging is locked by cmd.exe, antivirus,
        # Notepad, or another viewer. stdout remains the canonical build stream.
        return


def reset_log_file(header: str) -> None:
    if PARENT_REDIRECTS_BUILD_LOG:
        print(header, flush=True)
        return
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        LOG.write_text(header + "\n", encoding="utf-8")
    except OSError:
        print(header, flush=True)


def log(message: str) -> None:
    print(message, flush=True)
    _write_log_file_line(message)


def run(command: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    rendered = " ".join(f'"{item}"' if " " in item else item for item in command)
    log("[RUN] " + rendered)

    # If the .bat parent already redirects stdout to build_exe.log, inherit stdout
    # instead of opening the same file again. This fixes Windows PermissionError.
    if PARENT_REDIRECTS_BUILD_LOG:
        completed = subprocess.run(
            command,
            cwd=str(cwd) if cwd else None,
            env=env,
            stdout=sys.stdout,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
    else:
        try:
            LOG.parent.mkdir(parents=True, exist_ok=True)
            with LOG.open("a", encoding="utf-8", errors="replace") as handle:
                completed = subprocess.run(
                    command,
                    cwd=str(cwd) if cwd else None,
                    env=env,
                    stdout=handle,
                    stderr=subprocess.STDOUT,
                    text=True,
                    check=False,
                )
        except OSError:
            completed = subprocess.run(
                command,
                cwd=str(cwd) if cwd else None,
                env=env,
                stdout=sys.stdout,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
            )
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)


def is_bad_python(path: str) -> bool:
    lowered = path.lower().replace("/", "\\")
    bad_tokens = [
        "\\windowsapps\\",
        "\\codex-runtimes\\",
        "\\backend\\.venv\\",
        "\\dist\\worker_runtime\\",
    ]
    return any(token in lowered for token in bad_tokens)


def probe_python(command: list[str]) -> str:
    result = subprocess.run(
        command + ["-c", "import sys; print(getattr(sys, '_base_executable', sys.executable)); print(sys.executable); print(f'{sys.version_info.major}.{sys.version_info.minor}')"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return ""
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if len(lines) < 3:
        return ""
    base_exe, exe, version = lines[-3], lines[-2], lines[-1]
    chosen = base_exe if base_exe and Path(base_exe).exists() else exe
    if not Path(chosen).exists() or is_bad_python(chosen):
        return ""
    if version not in {"3.10", "3.11", "3.12", "3.13"}:
        return ""
    return chosen


def find_base_python() -> str:
    candidates: list[list[str]] = [["py", "-3.12"], ["py", "-3.13"], ["py", "-3.11"], ["py", "-3"], ["python"]]
    base_from_current = getattr(sys, "_base_executable", "") or sys.executable
    if base_from_current and Path(base_from_current).exists() and not is_bad_python(base_from_current):
        try:
            result = subprocess.run(
                [base_from_current, "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0 and result.stdout.strip() in {"3.10", "3.11", "3.12", "3.13"}:
                return str(Path(base_from_current))
        except Exception:
            pass
    for command in candidates:
        try:
            found = probe_python(command)
        except FileNotFoundError:
            found = ""
        if found:
            return found
    raise SystemExit("[ERROR] Normal Python 3.12/3.13 was not found. Install python.org Python and rerun build.")


def ensure_backend_venv(base_python: str) -> Path:
    venv_dir = ROOT / "backend" / ".venv"
    venv_py = venv_dir / "Scripts" / "python.exe"
    if not venv_py.exists():
        log("[INFO] Creating backend .venv")
        run([base_python, "-m", "venv", str(venv_dir)])
    if not venv_py.exists():
        raise SystemExit(f"[ERROR] backend venv python was not created: {venv_py}")
    # The build environment stays intact. The separate embedded worker determines
    # the packaged CPU/CUDA backend; installing root requirements would replace it.
    root_req = ROOT / "backend" / "requirements-base.txt"
    if root_req.exists():
        run([str(venv_py), "-m", "pip", "install", "-r", str(root_req)])
    else:
        run([str(venv_py), "-m", "pip", "install", "pyinstaller", "pywebview", "pystray", "Pillow"])
    # Build process needs PyInstaller/pywebview even if requirements.txt was edited.
    run([str(venv_py), "-m", "pip", "install", "pyinstaller", "pywebview", "pystray", "Pillow"])
    return venv_py


def clean_python_caches() -> None:
    for base in (ROOT / "backend", ROOT / "tools"):
        if not base.exists():
            continue
        for cache_dir in base.rglob("__pycache__"):
            if ".venv" not in cache_dir.parts:
                remove_build_tree(cache_dir)
    log("[OK] Removed stale __pycache__ folders from backend/tools before packaging")


def build_frontend() -> None:
    frontend = ROOT / "frontend"
    if not (frontend / "package.json").exists():
        raise SystemExit("[ERROR] frontend/package.json not found")
    node = shutil.which("node")
    npm_cli = Path(node).parent / "node_modules/npm/bin/npm-cli.js" if node else Path()
    npm = [node, str(npm_cli)] if node and npm_cli.is_file() else ["npm.cmd"]
    if not (frontend / "node_modules").exists():
        run(npm + ["ci"], cwd=frontend)
    env = os.environ.copy()
    env["VITE_API_BASE"] = "."
    env["NO_PROXY"] = "localhost,127.0.0.1,::1,[::1],*.localhost"
    env["no_proxy"] = env["NO_PROXY"]
    run(npm + ["run", "build"], cwd=frontend, env=env)
    if not (frontend / "dist" / "index.html").exists():
        raise SystemExit("[ERROR] frontend/dist/index.html was not created")


def build_pyinstaller(venv_py: Path) -> None:
    launcher = TOOLS / "exe_launcher.py"
    if not launcher.exists():
        raise SystemExit("[ERROR] tools/exe_launcher.py not found. Apply proxy bypass v34/v35 package first.")
    dist_exe = ROOT / "dist" / "LocalAIGPP.exe"
    if dist_exe.exists():
        try:
            dist_exe.unlink()
        except PermissionError as exc:
            raise SystemExit("[ERROR] Cannot replace dist/LocalAIGPP.exe. Close LocalAIGPP.exe from tray/Task Manager and rebuild.") from exc
    build_dir = OUT / "pyinstaller_build"
    spec_dir = OUT / "pyinstaller_spec"
    build_dir.mkdir(parents=True, exist_ok=True)
    spec_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["NO_PROXY"] = "localhost,127.0.0.1,::1,[::1],*.localhost"
    env["no_proxy"] = env["NO_PROXY"]
    env["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = "--no-proxy-server --proxy-bypass-list=<-loopback>;localhost;127.0.0.1;::1;[::1]"
    version_file = write_windows_version_file()
    cmd = [
        str(venv_py), "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onefile", "--windowed",
        "--name", "LocalAIGPP",
        "--icon", str(ROOT / "models_storage" / "branding" / "icons" / "mini_agent_head_v2.ico"),
        "--distpath", str(ROOT / "dist"),
        "--workpath", str(build_dir),
        "--specpath", str(spec_dir),
        "--paths", str(ROOT),
        "--version-file", str(version_file),
        "--add-data", f"{ROOT / 'VERSION'};.",
        "--add-data", f"{ROOT / 'frontend' / 'dist'};frontend_dist",
        "--add-data", f"{ROOT / 'models_storage' / 'branding' / 'icons'};branding_icons",
        "--collect-all", "webview",
        "--collect-all", "pystray",
        "--collect-all", "PIL",
        "--collect-submodules", "backend",
        "--collect-submodules", "uvicorn",
        "--exclude-module", "llama_cpp",
        "--exclude-module", "llama_cpp.llama_cpp",
        "--exclude-module", "llama_cpp.llama",
        "--exclude-module", "llama_cpp.llava_cpp",
        "--hidden-import", "uvicorn.logging",
        "--hidden-import", "uvicorn.loops.auto",
        "--hidden-import", "uvicorn.protocols.http.auto",
        "--hidden-import", "uvicorn.protocols.websockets.auto",
        "--hidden-import", "uvicorn.lifespan.on",
        "--hidden-import", "webview.platforms.edgechromium",
        "--hidden-import", "tkinter",
        "--hidden-import", "PIL.ImageTk",
        str(launcher),
    ]
    run(cmd, cwd=ROOT, env=env)
    if not dist_exe.exists():
        raise SystemExit("[ERROR] dist/LocalAIGPP.exe was not created")


def ignore_junk(_dir: str, names: list[str]) -> set[str]:
    return {name for name in names if name == "__pycache__" or name.endswith(".pyc") or name.endswith(".pyo")}


def _safe_model_folder_name(record: dict[str, object], fallback_index: int) -> str:
    raw = str(record.get("name") or record.get("id") or f"model_{fallback_index}").strip()
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in raw)
    return safe.strip("._")[:80] or f"model_{fallback_index}"


def _path_is_inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except Exception:
        return False


def remove_build_tree(target: Path) -> None:
    target = target.resolve()
    if target == ROOT.resolve() or not _path_is_inside(target, ROOT):
        raise ValueError(f"Refusing to delete outside the build workspace: {target}")
    if target.exists():
        shutil.rmtree(target)


def _copy_portable_model_registry(models_src: Path, models_dst: Path) -> None:
    src_registry = models_src / "models.json"
    dst_registry = models_dst / "models.json"
    if not src_registry.exists():
        dst_registry.write_text("[]", encoding="utf-8")
        log("[WARN] Source models.json not found. Wrote empty portable registry.")
        return

    try:
        registry = json.loads(src_registry.read_text(encoding="utf-8"))
    except Exception as exc:
        dst_registry.write_text("[]", encoding="utf-8")
        log(f"[WARN] Cannot read models.json ({exc}). Wrote empty portable registry.")
        return

    if not isinstance(registry, list):
        dst_registry.write_text("[]", encoding="utf-8")
        log("[WARN] models.json is not a list. Wrote empty portable registry.")
        return

    package_models = os.environ.get("LOCAL_AI_GPP_PACKAGE_MODELS", "1").strip().lower() not in {"0", "false", "no", "off"}
    portable: list[dict[str, object]] = []
    copied = 0
    missing = 0
    skipped = 0

    for index, item in enumerate(registry):
        if not isinstance(item, dict):
            continue
        record = dict(item)
        path_value = str(record.get("path") or "").strip()
        source_path = Path(path_value) if path_value else None
        if source_path and source_path.exists() and source_path.is_file() and source_path.suffix.lower() in MODEL_EXTENSIONS:
            if package_models:
                folder = _safe_model_folder_name(record, index)
                dest = models_dst / folder / source_path.name
                dest.parent.mkdir(parents=True, exist_ok=True)
                try:
                    if not dest.exists() or dest.stat().st_size != source_path.stat().st_size:
                        log(f"[INFO] Packaging model into dist: {source_path} -> {dest}")
                        shutil.copy2(source_path, dest)
                    record["path"] = str(Path("models_storage") / folder / source_path.name)
                    record["source"] = "packaged_file"
                    record["file_exists"] = True
                    record["file_size"] = dest.stat().st_size
                    copied += 1
                except Exception as exc:
                    log(f"[WARN] Failed to package model {source_path}: {exc}")
                    record["file_exists"] = False
                    record["validation_error"] = f"Model was not packaged: {exc}"
                    missing += 1
            else:
                record["file_exists"] = True
                record["file_size"] = source_path.stat().st_size
                skipped += 1
        else:
            if path_value.startswith("HUB::"):
                record["file_exists"] = False
                record["validation_error"] = "Remote hub model is not localized into dist."
            elif path_value:
                record["file_exists"] = False
                record["validation_error"] = f"Model file is missing on build machine: {path_value}"
            else:
                record["file_exists"] = False
                record["validation_error"] = "Model path is empty."
            missing += 1
        portable.append(record)

    dst_registry.write_text(json.dumps(portable, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"[OK] Portable models registry written: {dst_registry}")
    if package_models:
        log(f"[INFO] Model packaging summary: copied={copied}, missing={missing}")
    else:
        log(f"[INFO] Model packaging disabled by LOCAL_AI_GPP_PACKAGE_MODELS=0; external model paths kept/skipped={skipped}, missing={missing}")


def copy_portable_backend_and_metadata() -> None:
    dist = ROOT / "dist"
    dist.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "VERSION", dist / "VERSION")
    backend_src = ROOT / "backend" / "app"
    if not backend_src.exists():
        raise SystemExit(f"[ERROR] backend app not found: {backend_src}")
    backend_dst = dist / "backend"
    if backend_dst.exists():
        remove_build_tree(backend_dst)
    shutil.copytree(backend_src, backend_dst / "app", ignore=ignore_junk)
    (backend_dst / "__init__.py").write_text("", encoding="utf-8")
    for required in (backend_dst / "app" / "core.py", backend_dst / "app" / "llama_worker.py", backend_dst / "app" / "main.py"):
        if not required.exists():
            raise SystemExit(f"[ERROR] Required backend file is missing in dist: {required}")
    for name in ("requirements-base.txt", "requirements.txt"):
        src = ROOT / "backend" / name
        if src.exists():
            shutil.copy2(src, backend_dst / name)

    models_dst = dist / "models_storage"
    if models_dst.exists():
        # Keep packaged models only through the explicit registry copy below.
        remove_build_tree(models_dst)
    (models_dst / "branding").mkdir(parents=True, exist_ok=True)
    models_src = Path(os.environ.get("LOCAL_AI_GPP_DATA_DIR", str(ROOT))) / "models_storage"
    settings_src = models_src / "settings.json"
    if settings_src.exists():
        shutil.copy2(settings_src, models_dst / "settings.json")
    else:
        (models_dst / "settings.json").write_text("{}", encoding="utf-8")
    branding_src = models_src / "branding"
    if branding_src.exists():
        shutil.copytree(branding_src, models_dst / "branding", dirs_exist_ok=True, ignore=ignore_junk)
    _copy_portable_model_registry(models_src, models_dst)

    if (ROOT / "README.md").exists():
        shutil.copy2(ROOT / "README.md", dist / "README.md")
    for folder in ("docs", "examples"):
        if (ROOT / folder).is_dir():
            shutil.copytree(ROOT / folder, dist / folder, dirs_exist_ok=True, ignore=ignore_junk)
    log("[OK] Copied portable backend, settings, branding and model registry into dist")



def download_once(url: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.stat().st_size > 0:
        log(f"[OK] Using cached {target.name}")
        return
    log(f"[INFO] Downloading {url}")
    partial = target.with_suffix(target.suffix + ".partial")
    with urllib.request.urlopen(url, timeout=30) as response, partial.open("wb") as output:
        shutil.copyfileobj(response, output)
    partial.replace(target)


def configure_embedded_python(worker_runtime: Path) -> None:
    pth_files = sorted(worker_runtime.glob("python*._pth"))
    if not pth_files:
        raise SystemExit(f"[ERROR] Embedded python ._pth file not found in {worker_runtime}")
    pth = pth_files[0]
    # '..' is critical: python.exe lives in dist\worker_runtime, while backend package lives in dist\backend.
    # Without this, worker_runtime can install packages but cannot import backend.app.llama_worker on another PC.
    pth.write_text(
        "python312.zip\n"
        ".\n"
        "Lib\n"
        "Lib\\site-packages\n"
        "..\n"
        "import site\n",
        encoding="utf-8",
        newline="\n",
    )
    log(f"[OK] Patched embedded Python path file: {pth}")


def verify_worker_runtime(worker_py: Path, runtime_kind: str, dist: Path) -> dict[str, object]:
    script = (
        "import sys,json; "
        "from backend.app.cuda_runtime import configure_cuda_dlls; configure_cuda_dlls(); "
        "import backend.app.llama_worker, llama_cpp; "
        "from llama_cpp import llama_cpp as lib; "
        "print(json.dumps({'python':sys.executable,'llama_cpp':llama_cpp.__version__,"
        "'supports_gpu_offload':bool(lib.llama_supports_gpu_offload()),"
        "'system_info':lib.llama_print_system_info().decode('utf-8','replace')}))"
    )
    checked = subprocess.run([str(worker_py), "-c", script], cwd=str(dist),
                             capture_output=True, text=True, encoding="utf-8", errors="replace")
    if checked.returncode:
        raise SystemExit("[ERROR] Packaged runtime failed verification: " + (checked.stderr or checked.stdout))
    details = json.loads(checked.stdout.strip().splitlines()[-1])
    if runtime_kind.startswith("cu") and (
        not details.get("supports_gpu_offload") or "CUDA" not in str(details.get("system_info", "")).upper()
    ):
        raise SystemExit("[ERROR] CUDA was selected, but the packaged worker has no CUDA offload. Build stopped.")
    log("[OK] Verified packaged runtime: " + json.dumps(details, ensure_ascii=False))
    return details


def create_worker_runtime(runtime_kind: str, dist: Path | None = None) -> None:
    dist = dist if dist is not None else ROOT / "dist"
    worker_runtime = dist / "worker_runtime"
    worker_py = worker_runtime / "python.exe"
    if worker_runtime.exists():
        remove_build_tree(worker_runtime)
    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    embed_zip = DOWNLOADS / PY_EMBED_ZIP
    get_pip = DOWNLOADS / "get-pip.py"
    download_once(PY_EMBED_URL, embed_zip)
    download_once(GET_PIP_URL, get_pip)
    worker_runtime.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(embed_zip, "r") as archive:
        archive.extractall(worker_runtime)
    configure_embedded_python(worker_runtime)
    if not worker_py.exists():
        raise SystemExit(f"[ERROR] Embedded worker python missing: {worker_py}")

    run([str(worker_py), str(get_pip), "--no-warn-script-location"], cwd=dist)
    req = ROOT / "backend" / "requirements-base.txt"
    if req.exists():
        run([str(worker_py), "-m", "pip", "install", "--no-warn-script-location", "-r", str(req)], cwd=dist)
    else:
        run([str(worker_py), "-m", "pip", "install", "--no-warn-script-location", "fastapi==0.116.1", "uvicorn==0.35.0", "python-multipart==0.0.20", "httpx==0.28.1"], cwd=dist)

    runtime_kind = (runtime_kind or "cpu").lower()
    if runtime_kind.startswith("cu"):
        cached_wheel = os.environ.get("LOCAL_AI_GPP_CUDA_WHEEL")
        if not cached_wheel:
            wheel_dir = DOWNLOADS / f"llama-{CUDA_VERSION}-{runtime_kind}"
            wheel_dir.mkdir(parents=True, exist_ok=True)
            # Fetch only from the CUDA index; PyPI must not supply a CPU wheel.
            run([str(worker_py), "-m", "pip", "download", "--no-deps", "--only-binary=:all:",
                 "--index-url", f"https://abetlen.github.io/llama-cpp-python/whl/{runtime_kind}",
                 "--dest", str(wheel_dir), f"llama-cpp-python=={CUDA_VERSION}"], cwd=dist)
            wheels = list(wheel_dir.glob("llama_cpp_python-*.whl"))
            if len(wheels) != 1:
                raise SystemExit("[ERROR] Expected exactly one CUDA wheel in " + str(wheel_dir))
            cached_wheel = str(wheels[0])
        run([str(worker_py), "-m", "pip", "install", "--no-warn-script-location", "--only-binary=:all:", cached_wheel], cwd=dist)
        dll_source = Path(os.environ.get("LOCAL_AI_GPP_CUDA_DLL_DIR", str(ROOT / "backend/.venv/cuda")))
        dlls = ("cudart64_12.dll", "cublas64_12.dll", "cublasLt64_12.dll")
        if all((dll_source / name).is_file() for name in dlls):
            (worker_runtime / "cuda").mkdir(exist_ok=True)
            for name in dlls:
                shutil.copy2(dll_source / name, worker_runtime / "cuda" / name)
            for license_file in dll_source.glob('*LICENSE*.txt'):
                shutil.copy2(license_file, worker_runtime / 'cuda' / license_file.name)
        else:
            run([str(worker_py), "-m", "pip", "install", "--no-warn-script-location", "nvidia-cuda-runtime-cu12==12.4.127", "nvidia-cublas-cu12==12.4.5.8"], cwd=dist)
        packaged_dll_dirs = [worker_runtime / "cuda",
                             worker_runtime / "Lib/site-packages/nvidia/cuda_runtime/bin",
                             worker_runtime / "Lib/site-packages/nvidia/cublas/bin"]
        missing_dlls = [name for name in dlls if not any((folder / name).is_file() for folder in packaged_dll_dirs)]
        if missing_dlls:
            raise SystemExit("[ERROR] CUDA DLLs are missing from the portable worker: " + ", ".join(missing_dlls))
    else:
        run([str(worker_py), "-m", "pip", "install", "--no-warn-script-location", "--force-reinstall", "--no-cache-dir", f"llama-cpp-python=={CPU_VERSION}", "--extra-index-url", "https://abetlen.github.io/llama-cpp-python/whl/cpu"], cwd=dist)

    verified = verify_worker_runtime(worker_py, runtime_kind, dist)
    (dist / "runtime_info.json").write_text(
        json.dumps(
            {
                "effective": runtime_kind,
                "supports_gpu_offload": verified["supports_gpu_offload"],
                "llama_cpp_version": verified["llama_cpp"],
                "worker_python": "worker_runtime\\python.exe",
                "python_embed_version": PY_EMBED_VERSION,
                "backend_path_mode": "python._pth contains ..",
                "proxy_bypass": True,
                "built_at": datetime.now().isoformat(timespec="seconds"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    log("[OK] worker_runtime verified")


def write_dist_portable_helpers(runtime_kind: str) -> None:
    """Write tiny self-check helpers directly into dist.

    On a copied machine the user may have only the dist folder, not the project
    tools folder. These helpers use the embedded worker_runtime\\python.exe, so
    they do not require installed Python.
    """
    dist = ROOT / "dist"
    readme = dist / "README_PORTABLE_DIST.txt"
    shutil.copy2(ROOT / "models_storage/branding/icons/mini_agent_head_v2.ico", dist / "LocalAIGPP.ico")
    readme.write_text(
        "Local AI GPP portable dist\n"
        "==========================\n\n"
        "Переносить нужно всю папку dist целиком, не один LocalAIGPP.exe.\n\n"
        "Минимальный состав:\n"
        "  LocalAIGPP.exe\n"
        "  worker_runtime\\python.exe\n"
        "  backend\\app\\llama_worker.py\n"
        "  models_storage\\models.json\n"
        "  models_storage\\<model>\\*.gguf\n\n"
        "Проверка на целевой машине:\n"
        "  CHECK_DIST_HEALTH.bat\n\n"
        "Запуск:\n"
        "  RUN_LocalAIGPP.bat\n\n"
        "Если окно не открывается, проверьте наличие Microsoft Edge WebView2 Runtime.\n"
        "Если модель не отвечает, запустите CHECK_DIST_HEALTH.bat и пришлите вывод.\n",
        encoding="utf-8",
    )

    (dist / "RUN_LocalAIGPP.bat").write_text(
        "@echo off\n"
        "setlocal EnableExtensions\n"
        "chcp 65001 >nul\n"
        "cd /d \"%~dp0\"\n"
        "set \"NO_PROXY=localhost,127.0.0.1,::1,[::1],*.localhost\"\n"
        "set \"no_proxy=%NO_PROXY%\"\n"
        "set \"WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--no-proxy-server --proxy-bypass-list=<-loopback>;localhost;127.0.0.1;::1;[::1]\"\n"
        "start \"\" \"%~dp0LocalAIGPP.exe\"\n",
        encoding="utf-8",
        newline="\r\n",
    )

    health_py = (
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "root = Path(__file__).resolve().parent\n"
        "print('Local AI GPP dist health check')\n"
        "print('root=', root)\n"
        "required = [root/'LocalAIGPP.exe', root/'worker_runtime'/'python.exe', root/'backend'/'app'/'llama_worker.py', root/'models_storage'/'models.json']\n"
        "errors = 0\n"
        "for path in required:\n"
        "    ok = path.exists()\n"
        "    print(('[OK] ' if ok else '[ERROR] ') + str(path.relative_to(root)))\n"
        "    errors += 0 if ok else 1\n"
        "pth = root/'worker_runtime'/'python312._pth'\n"
        "if pth.exists():\n"
        "    lines = [x.strip().lstrip('\\ufeff') for x in pth.read_text(encoding='utf-8', errors='replace').splitlines()]\n"
        "    print(('[OK] ' if '..' in lines else '[ERROR] ') + 'worker_runtime/python312._pth contains ..')\n"
        "    errors += 0 if '..' in lines else 1\n"
        "try:\n"
        "    from backend.app.cuda_runtime import configure_cuda_dlls\n"
        "    configure_cuda_dlls()\n"
        "    import backend.app.llama_worker, llama_cpp\n"
        "    print('[OK] import backend.app.llama_worker')\n"
        "    print('[OK] import llama_cpp version=', getattr(llama_cpp, '__version__', 'unknown'))\n"
        "except Exception as exc:\n"
        "    print('[ERROR] worker import failed:', repr(exc))\n"
        "    errors += 1\n"
        "try:\n"
        "    models = json.loads((root/'models_storage'/'models.json').read_text(encoding='utf-8'))\n"
        "except Exception as exc:\n"
        "    print('[ERROR] cannot read models.json:', repr(exc))\n"
        "    models = []\n"
        "    errors += 1\n"
        "if isinstance(models, list):\n"
        "    llms = [m for m in models if isinstance(m, dict) and m.get('type') == 'LLM']\n"
        "    print('LLM models=', len(llms))\n"
        "    for m in llms:\n"
        "        p = Path(str(m.get('path') or ''))\n"
        "        if not p.is_absolute(): p = root / p\n"
        "        inside = False\n"
        "        try:\n"
        "            p.resolve().relative_to(root.resolve())\n"
        "            inside = True\n"
        "        except Exception:\n"
        "            inside = False\n"
        "        ok = p.exists() and inside\n"
        "        print(('[OK] ' if ok else '[ERROR] ') + str(m.get('id')) + ' -> ' + str(p))\n"
        "        errors += 0 if ok else 1\n"
        "print('errors=', errors)\n"
        "raise SystemExit(0 if errors == 0 else 1)\n"
    )
    (dist / "_dist_health_check.py").write_text(health_py, encoding="utf-8")
    (dist / "CHECK_DIST_HEALTH.bat").write_text(
        "@echo off\n"
        "setlocal EnableExtensions\n"
        "chcp 65001 >nul\n"
        "cd /d \"%~dp0\"\n"
        "echo ============================================================\n"
        "echo Local AI GPP - portable dist health check\n"
        "echo ============================================================\n"
        "if not exist \"worker_runtime\\python.exe\" (\n"
        "  echo [ERROR] worker_runtime\\python.exe not found. Copy the whole dist folder.\n"
        "  pause\n"
        "  exit /b 1\n"
        ")\n"
        "\"%~dp0worker_runtime\\python.exe\" \"%~dp0_dist_health_check.py\"\n"
        "set \"RC=%ERRORLEVEL%\"\n"
        "echo.\n"
        "if not \"%RC%\"==\"0\" (\n"
        "  echo FAILED. Send this output and logs folder.\n"
        ") else (\n"
        "  echo OK. Dist structure is portable.\n"
        ")\n"
        "pause\n"
        "exit /b %RC%\n",
        encoding="utf-8",
        newline="\r\n",
    )
    log(f"[OK] Wrote portable dist helpers for target machine checks: {dist / 'CHECK_DIST_HEALTH.bat'}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    runtime = parser.add_mutually_exclusive_group()
    runtime.add_argument("--cpu", action="store_true", help="Explicitly build CPU runtime; NVIDIA enables CUDA automatically otherwise")
    runtime.add_argument("--cuda", nargs="?", const="auto", default=None, help="Require CUDA worker runtime, e.g. --cuda auto or --cuda cu124")
    parser.add_argument("--no-models", action="store_true", help="Do not copy registered model files into dist; keep metadata only")
    return parser.parse_args()


def find_nvidia_smi() -> str:
    candidates = [shutil.which("nvidia-smi")]
    if os.name == "nt":
        candidates += [str(Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/nvidia-smi.exe"),
                       str(Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "NVIDIA Corporation/NVSMI/nvidia-smi.exe")]
    return next((candidate for candidate in candidates if candidate and Path(candidate).is_file()), "")


def choose_runtime_kind(args: argparse.Namespace) -> str:
    if args.cpu:
        return "cpu"
    if args.cuda is None and not find_nvidia_smi():
        info_path = ROOT / "dist/runtime_info.json"
        previous = json.loads(info_path.read_text(encoding="utf-8")) if info_path.is_file() else {}
        if str(previous.get("effective", "")).startswith("cu"):
            return str(previous["effective"])
        log("[INFO] NVIDIA was not detected. Building CPU runtime; --cuda cu124 forces a CUDA package.")
        return "cpu"
    requested = str(args.cuda or "auto").strip().lower()
    if requested in {"", "auto", "cuda", "gpu"}:
        requested = os.environ.get("LOCAL_AI_GPP_DEFAULT_CUDA_TAG", "cu124")
    if not requested.startswith("cu") or not requested[2:].isdigit():
        raise SystemExit(f"[ERROR] Unsupported CUDA tag '{requested}'. Expected cu124/cu125/etc.")
    return requested


def prepare_worker_runtime(runtime_kind: str) -> Path:
    stage = OUT / "worker_stage"
    if stage.exists():
        remove_build_tree(stage)
    shutil.copytree(ROOT / "backend/app", stage / "backend/app", ignore=ignore_junk)
    (stage / "backend/__init__.py").write_text("", encoding="utf-8")
    shutil.copy2(ROOT / "VERSION", stage / "VERSION")
    create_worker_runtime(runtime_kind, stage)
    return stage


def main() -> int:
    args = parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    reset_log_file("Local AI GPP desktop exe builder v67.4")
    runtime_kind = choose_runtime_kind(args)
    log("=" * 60)
    log("Local AI GPP - build desktop EXE")
    log("=" * 60)
    log(f"Root: {ROOT}")
    log(f"Runtime: {runtime_kind}")
    base_python = find_base_python()
    log(f"[INFO] build/base python: {base_python}")
    venv_py = ensure_backend_venv(base_python)
    if getattr(args, "no_models", False):
        os.environ["LOCAL_AI_GPP_PACKAGE_MODELS"] = "0"
    # Install and verify CUDA plus its DLLs before replacing the current EXE.
    worker_stage = prepare_worker_runtime(runtime_kind)
    clean_python_caches()
    build_frontend()
    build_pyinstaller(venv_py)
    copy_portable_backend_and_metadata()
    worker_target = ROOT / "dist/worker_runtime"
    if worker_target.exists():
        remove_build_tree(worker_target)
    shutil.move(str(worker_stage / "worker_runtime"), str(worker_target))
    shutil.copy2(worker_stage / "runtime_info.json", ROOT / "dist/runtime_info.json")
    write_dist_portable_helpers(runtime_kind)
    log("")
    log("=" * 60)
    log("DESKTOP EXE BUILD COMPLETE")
    log("=" * 60)
    log(f"File: {ROOT / 'dist' / 'LocalAIGPP.exe'}")
    log(f"Worker: {ROOT / 'dist' / 'worker_runtime' / 'python.exe'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
