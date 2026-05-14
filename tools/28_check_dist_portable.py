from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
MODEL_EXTENSIONS = {".gguf", ".bin", ".safetensors"}


def is_inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except Exception:
        return False


def ok(msg: str) -> None:
    print(f"[OK] {msg}")


def warn(msg: str) -> None:
    print(f"[WARN] {msg}")


def error(msg: str) -> None:
    print(f"[ERROR] {msg}")


def require(path: Path, name: str, errors: list[str]) -> None:
    if path.exists():
        ok(f"{name}: {path}")
    else:
        errors.append(f"Missing {name}: {path}")
        error(errors[-1])


def main() -> int:
    errors: list[str] = []
    warnings: list[str] = []

    print("============================================================")
    print("Local AI GPP - dist portability check v67.4")
    print("============================================================")
    print(f"Project root: {ROOT}")
    print(f"Dist:         {DIST}")
    print()

    require(DIST / "LocalAIGPP.exe", "desktop EXE", errors)
    require(DIST / "worker_runtime" / "python.exe", "embedded worker Python", errors)
    require(DIST / "worker_runtime" / "python312._pth", "embedded Python ._pth", errors)
    require(DIST / "backend" / "app" / "llama_worker.py", "portable llama worker", errors)
    require(DIST / "backend" / "app" / "core.py", "portable backend core", errors)
    require(DIST / "models_storage" / "settings.json", "portable settings", errors)
    require(DIST / "models_storage" / "models.json", "portable model registry", errors)
    require(DIST / "runtime_info.json", "runtime info", errors)

    pth = DIST / "worker_runtime" / "python312._pth"
    if pth.exists():
        lines = [line.strip() for line in pth.read_text(encoding="utf-8", errors="replace").splitlines()]
        if ".." in lines:
            ok("worker_runtime python312._pth contains exact '..' line")
        else:
            errors.append("worker_runtime python312._pth does not contain exact '..' line")
            error(errors[-1])

    runtime_info = DIST / "runtime_info.json"
    if runtime_info.exists():
        try:
            info = json.loads(runtime_info.read_text(encoding="utf-8", errors="replace"))
            ok(f"runtime_info effective={info.get('effective')} worker={info.get('worker_python')}")
        except Exception as exc:
            warnings.append(f"Cannot parse runtime_info.json: {exc}")
            warn(warnings[-1])

    registry_path = DIST / "models_storage" / "models.json"
    packaged_llm_count = 0
    missing_model_count = 0
    external_model_count = 0
    if registry_path.exists():
        try:
            registry = json.loads(registry_path.read_text(encoding="utf-8", errors="replace"))
        except Exception as exc:
            registry = []
            errors.append(f"Cannot parse dist models.json: {exc}")
            error(errors[-1])
        if not isinstance(registry, list):
            registry = []
            errors.append("dist models.json is not a list")
            error(errors[-1])

        llm_records = [item for item in registry if isinstance(item, dict) and str(item.get("type") or "") == "LLM"]
        if not llm_records:
            warnings.append("No LLM records in dist\\models_storage\\models.json. App can start, but there is no model to answer.")
            warn(warnings[-1])

        for item in llm_records:
            model_id = str(item.get("id") or item.get("name") or "<unknown>")
            path_value = str(item.get("path") or "").strip()
            if not path_value:
                missing_model_count += 1
                errors.append(f"Model {model_id} has empty path in dist registry")
                error(errors[-1])
                continue
            raw_model_path = Path(path_value)
            if raw_model_path.is_absolute():
                model_path = raw_model_path
                errors.append(f"Model {model_id} has absolute path in dist registry. Portable registry must use relative models_storage path: {model_path}")
                error(errors[-1])
            else:
                normalized = path_value.replace('\\', '/')
                model_path = (DIST / raw_model_path) if normalized.startswith('models_storage/') else (DIST / 'models_storage' / raw_model_path)
            if not model_path.exists() or not model_path.is_file():
                missing_model_count += 1
                errors.append(f"Model {model_id} file is missing for copied dist: {model_path}")
                error(errors[-1])
                continue
            if model_path.suffix.lower() not in MODEL_EXTENSIONS:
                warnings.append(f"Model {model_id} has unusual extension: {model_path}")
                warn(warnings[-1])
            if is_inside(model_path, DIST):
                packaged_llm_count += 1
                ok(f"Packaged LLM: {model_id} -> {model_path.name} ({model_path.stat().st_size} bytes)")
            else:
                external_model_count += 1
                errors.append(f"Model {model_id} points outside dist. It will likely fail on another PC: {model_path}")
                error(errors[-1])

    site_packages = DIST / "worker_runtime" / "Lib" / "site-packages"
    if site_packages.exists():
        llama_candidates = list(site_packages.glob("llama_cpp*"))
        if llama_candidates:
            ok("llama-cpp-python package exists in worker_runtime")
        else:
            errors.append("llama-cpp-python package is missing in worker_runtime\\Lib\\site-packages")
            error(errors[-1])

    print()
    print("Summary:")
    print(f"  packaged_llm_count={packaged_llm_count}")
    print(f"  missing_model_count={missing_model_count}")
    print(f"  external_model_count={external_model_count}")
    print(f"  warnings={len(warnings)}")
    print(f"  errors={len(errors)}")

    if errors:
        print()
        print("FAILED: dist is not portable yet. Rebuild with tools\\02_build_exe.bat --cpu and do not use --no-models if you want model files inside dist.")
        return 1
    print()
    print("OK: dist has the required portable structure.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
