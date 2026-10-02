from __future__ import annotations

import json
import contextlib
import inspect
import os
import platform
import re
import subprocess
import shutil
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import HTTPException

APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else APP_DIR.parent.parent
MODELS_DIR = Path(os.getenv("LOCAL_AI_GPP_DATA_DIR", str(PROJECT_ROOT))) / "models_storage"
LOGS_DIR = Path(os.getenv("LOCAL_AI_GPP_DATA_DIR", str(PROJECT_ROOT))) / "logs"
BRANDING_DIR = MODELS_DIR / "branding"
MODELS_FILE = MODELS_DIR / "models.json"
SETTINGS_FILE = MODELS_DIR / "settings.json"

MODELS_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)
BRANDING_DIR.mkdir(parents=True, exist_ok=True)

RUNTIMES: dict[str, dict[str, Any]] = {}
WORKER_FIRST_EVENT_TIMEOUT_SEC = 30
WORKER_IDLE_TIMEOUT_SEC = 60
RUNTIME_LOCK = threading.Lock()
JSON_LOCK = threading.RLock()


def _subprocess_no_window_kwargs() -> dict[str, Any]:
    if os.name != "nt":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 1)
    startupinfo.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
    return {
        "startupinfo": startupinfo,
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
    }


def _worker_env(request_log_path: Path | None = None) -> dict[str, str]:
    env = os.environ.copy()
    for key in list(env):
        upper_key = key.upper()
        if upper_key.startswith("_PYI_") or upper_key.startswith("PYINSTALLER_"):
            env.pop(key, None)
        elif upper_key in {"PYTHONHOME", "PYTHONPATH", "__PYVENV_LAUNCHER__"}:
            env.pop(key, None)

    worker_python = external_worker_python()
    if worker_python:
        worker_python_path = Path(worker_python)
        scripts_dir = worker_python_path.parent
        venv_dir = scripts_dir.parent
        env["VIRTUAL_ENV"] = str(venv_dir)
        path_parts = [str(scripts_dir)]
        for part in str(env.get("PATH", "")).split(os.pathsep):
            if not part:
                continue
            lowered = part.lower()
            if "_mei" in lowered:
                continue
            path_parts.append(part)
        env["PATH"] = os.pathsep.join(dict.fromkeys(path_parts))

    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    if request_log_path:
        env["LOCAL_AI_GPP_WORKER_TRACE"] = str(request_log_path)
    return env


@contextlib.contextmanager
def _clean_subprocess_dll_search_path():
    if os.name != "nt" or not getattr(sys, "frozen", False):
        yield
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        previous = getattr(sys, "_MEIPASS", None)
        kernel32.SetDllDirectoryW(None)
        try:
            yield
        finally:
            if previous:
                kernel32.SetDllDirectoryW(str(previous))
    except Exception:
        yield

DEFAULT_SETTINGS: dict[str, Any] = {
    "branding": {
        "title": "Агент ГПП",
        "subtitle": "Локальный движок LLM моделей.",
        "logo_url": "",
        "logo_width": 150,
        "logo_height": 78,
        "logo_radius": 16,
        "logo_padding": 10,
        "logo_fit": "contain",
    },
    "theme": {
        "accent": "#2f6fed",
        "hero_text": "#101828",
        "chrome_start": "#063f2c",
        "chrome_end": "#0b5d43",
        "chrome_text": "#ffffff",
        "background_start": "#f6f7fb",
        "background_end": "#e8ecf4",
        "panel": "#ffffff",
        "panel_alt": "#f2f4f7",
        "border": "#d0d5dd",
        "text": "#101828",
        "muted": "#667085",
        "user_bubble": "#dbeafe",
        "assistant_bubble": "#ffffff",
        "success": "#168a4a",
        "warning": "#c27803",
        "danger": "#c2413b",
    },
    "layout": {
        "app_max_width": 1920,
        "card_radius": 8,
        "hero_compact": False,
        "left_panel_width": 250,
        "center_panel_min_width": 440,
        "right_panel_width": 620,
    },
    "runtime": {
        "n_ctx": 4096,
        "n_batch": 512,
        "n_threads": max(1, os.cpu_count() or 4),
        "n_threads_batch": 0,
        "n_gpu_layers": 0,
        "main_gpu": 0,
        "split_mode": "layer",
        "tensor_split": "",
        "temperature": 0.2,
        "enable_thinking": False,
        "max_tokens": 1024,
        "top_k": 40,
        "top_p": 0.95,
        "min_p": 0.05,
        "repeat_penalty": 1.1,
        "seed": -1,
        "offload_kqv": True,
        "flash_attn": False,
        "op_offload": True,
        "swa_full": False,
        "use_mmap": True,
        "use_mlock": False,
        "verbose_runtime": False,
        "gpu_fallback_to_cpu": True,
        "warm_policy": "unload_after_idle",
        "idle_unload_sec": 1800,
        "preload_on_start": False,
        "request_timeout_sec": 180,
        "load_timeout_sec": 120,
        "worker_idle_timeout_sec": 60,
    },
    "server": {
        "host": "127.0.0.1",
        "port": 8765,
        "public_base_url": "http://127.0.0.1:8765",
        "openai_compat_enabled": True,
        "openai_compat_path": "/v1/chat/completions",
        "cors_origins": [
            "http://127.0.0.1:5174",
            "http://localhost:5174",
            "http://127.0.0.1:5173",
            "http://localhost:5173",
            "http://127.0.0.1:8080",
            "http://localhost:8080",
        ],
        "api_key": "",
    },
    "hub": {
        "enabled": False,
        "base_url": "",
        "models_endpoint": "/models",
        "pull_endpoint": "/models/pull",
        "token": "",
        "timeout_sec": 30,
    },
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_request_log(model_id: str, kind: str = "chat") -> tuple[str, Path]:
    safe_model = re.sub(r"[^A-Za-z0-9_.-]+", "_", model_id or "model").strip("_")[:80] or "model"
    request_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    path = LOGS_DIR / f"{request_id}_{kind}_{safe_model}.log"
    append_request_log(
        path,
        "request_created",
        {
            "request_id": request_id,
            "kind": kind,
            "model_id": model_id,
            "project_root": str(PROJECT_ROOT),
            "frozen_exe": bool(getattr(sys, "frozen", False)),
            "python": sys.executable,
            "pid": os.getpid(),
        },
    )
    return request_id, path


def append_request_log(path: Path | str | None, title: str, data: Any | None = None) -> None:
    if not path:
        return
    log_path = Path(path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {title}\n")
        if data is not None:
            if isinstance(data, str):
                handle.write(data)
                if not data.endswith("\n"):
                    handle.write("\n")
            else:
                handle.write(json.dumps(data, ensure_ascii=False, indent=2, default=str))
                handle.write("\n")


def read_log_tail(path: Path | str | None, max_lines: int = 160) -> list[str]:
    if not path:
        return []
    log_path = Path(path)
    if not log_path.exists():
        return []
    try:
        return log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-max_lines:]
    except Exception:
        return []


def _write_json(path: Path, data: Any) -> None:
    with JSON_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def _read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        _write_json(path, fallback)
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HTTPException(503, f"Cannot read {path.name}; original file preserved: {exc}") from exc


def merge_dict(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    result = json.loads(json.dumps(base))
    for key, value in incoming.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = merge_dict(result[key], value)
        else:
            result[key] = value
    return result


def get_logo_url() -> str:
    # Branding root can contain helper folders like branding/icons for the EXE icon.
    # The UI logo must only be an actual logo.* image, never the first random file.
    allowed = {".svg", ".png", ".jpg", ".jpeg", ".webp"}
    candidates = []
    for file in sorted(BRANDING_DIR.iterdir()):
        if not file.is_file():
            continue
        if file.suffix.lower() not in allowed:
            continue
        if file.stem.lower() != "logo":
            continue
        candidates.append(file)
    if not candidates:
        return ""
    file = candidates[0]
    return f"/assets/branding/{file.name}?v={int(file.stat().st_mtime)}"


def load_settings() -> dict[str, Any]:
    raw = _read_json(SETTINGS_FILE, DEFAULT_SETTINGS)
    merged = merge_dict(DEFAULT_SETTINGS, raw if isinstance(raw, dict) else {})
    merged["branding"]["logo_url"] = get_logo_url()
    return merged


def save_settings(payload: dict[str, Any]) -> dict[str, Any]:
    merged = merge_dict(DEFAULT_SETTINGS, payload if isinstance(payload, dict) else {})
    merged["branding"]["logo_url"] = get_logo_url()
    _write_json(SETTINGS_FILE, merged)
    return merged


def _is_hub_model_path(path_value: str) -> bool:
    return path_value.startswith("HUB::")


def resolve_model_file_path(path_value: Any) -> Path:
    raw = str(path_value or "").strip().strip('"')
    path_obj = Path(raw)
    if path_obj.is_absolute():
        return path_obj
    normalized = raw.replace('\\', '/')
    candidates: list[Path] = []
    if normalized.startswith('models_storage/'):
        candidates.append(MODELS_DIR.parent / path_obj)
    else:
        candidates.append(MODELS_DIR / path_obj)
        candidates.append(PROJECT_ROOT / path_obj)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0] if candidates else path_obj


def validate_model_record(model: dict[str, Any]) -> dict[str, Any]:
    item = dict(model)
    path_value = str(item.get("path") or "").strip()
    item["file_exists"] = False
    item["file_size"] = 0
    item.pop("validation_error", None)

    if _is_hub_model_path(path_value):
        item["status"] = item.get("status") or "remote"
        item["validation_error"] = "Модель из репозитория еще не локализирована в локальный файл."
        return item

    if not path_value:
        item["status"] = "missing"
        item["validation_error"] = "Путь к файлу модели пустой."
        return item

    path_obj = resolve_model_file_path(path_value)
    if not path_obj.exists() or not path_obj.is_file():
        item["status"] = "missing"
        item["validation_error"] = f"Файл модели не найден: {path_obj}"
        item["resolved_path"] = str(path_obj)
        return item

    item["path"] = str(path_obj)
    item["resolved_path"] = str(path_obj)
    item["file_exists"] = True
    item["file_size"] = path_obj.stat().st_size
    if item.get("status") == "missing":
        item["status"] = "saved"
    return item


def validate_model_registry(*, save: bool = False) -> list[dict[str, Any]]:
    data = _read_json(MODELS_FILE, [])
    if not isinstance(data, list):
        return []
    models = [validate_model_record(item) for item in data if isinstance(item, dict)]
    if save:
        save_models(models)
    return models


def load_models() -> list[dict[str, Any]]:
    return validate_model_registry(save=False)


def save_models(models: list[dict[str, Any]]) -> None:
    stored = []
    for model in models:
        record = dict(model)
        path = Path(str(record.get('path') or ''))
        if path.is_absolute():
            try:
                relative = path.resolve().relative_to(MODELS_DIR.resolve())
                record['path'] = str(Path('models_storage') / relative)
                record.pop('resolved_path', None)
            except ValueError:
                pass
        stored.append(record)
    _write_json(MODELS_FILE, stored)


def upload_logo_file(file_obj: Any, filename: str) -> str:
    # LOGO_UPLOAD_SAFE_V61: logo and EXE icon are separate assets.
    # Logo lives as models_storage/branding/logo.<ext>.
    # EXE icon lives as models_storage/branding/icons/local_ai_gpp.ico.
    ext = Path(filename or "logo.png").suffix.lower()
    if ext not in {".svg", ".png", ".jpg", ".jpeg", ".webp"}:
        raise HTTPException(status_code=400, detail="Поддерживаются SVG, PNG, JPG, JPEG, WEBP")

    BRANDING_DIR.mkdir(parents=True, exist_ok=True)
    target = BRANDING_DIR / f"logo{ext}"
    tmp_target = BRANDING_DIR / f"logo_upload_tmp{ext}"

    try:
        if tmp_target.exists():
            tmp_target.unlink()
        with tmp_target.open("wb") as handle:
            shutil.copyfileobj(file_obj, handle)
        if not tmp_target.exists() or tmp_target.stat().st_size <= 0:
            raise HTTPException(status_code=400, detail="Файл логотипа пустой или не прочитан.")

        for item in BRANDING_DIR.iterdir():
            if item.is_file() and item.stem.lower() == "logo" and item.name != tmp_target.name:
                item.unlink()
        tmp_target.replace(target)
        return get_logo_url()
    except HTTPException:
        raise
    except Exception as exc:
        try:
            if tmp_target.exists():
                tmp_target.unlink()
        except Exception:
            pass
        raise HTTPException(status_code=400, detail=f"Не удалось загрузить логотип: {exc}") from exc


def sanitize_model_name(name: str) -> str:
    cleaned = (name or "").strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="model_name is required")
    for ch in ('/', '\\', ':', '*', '?', '"', '<', '>', '|'):
        if ch in cleaned:
            raise HTTPException(status_code=400, detail="model_name contains unsupported characters")
    return cleaned


def make_model_record(
    *,
    name: str,
    model_type: str,
    filename: str,
    path: str,
    source: str,
    runtime: dict[str, Any] | None = None,
) -> dict[str, Any]:
    safe_name = sanitize_model_name(name)
    return {
        "id": f"{safe_name}:{Path(filename).name}",
        "name": safe_name,
        "type": model_type,
        "filename": Path(filename).name,
        "path": str(path),
        "status": "saved",
        "source": source,
        "uploaded_at": now_iso(),
        "started_at": None,
        "runtime": runtime or {},
    }


def upsert_model(record: dict[str, Any]) -> dict[str, Any]:
    checked = validate_model_record(record)
    with JSON_LOCK:
        models = [m for m in load_models() if m.get("id") != checked.get("id")]
        models.insert(0, checked)
        save_models(models)
    return checked


def delete_model(model_id: str) -> None:
    with runtime_operation(), JSON_LOCK:
        models = load_models()
        next_models = [m for m in models if m.get("id") != model_id]
        if len(next_models) == len(models):
            raise HTTPException(status_code=404, detail="Model not found")
        _unload_runtime(model_id)
        save_models(next_models)


def find_model(model_id: str) -> tuple[list[dict[str, Any]], dict[str, Any], int]:
    models = load_models()
    for idx, model in enumerate(models):
        if model.get("id") == model_id:
            return models, model, idx
    raise HTTPException(status_code=404, detail="Model not found")


def copy_uploaded_model(name: str, incoming_filename: str, file_obj: Any) -> dict[str, Any]:
    safe_name = sanitize_model_name(name)
    model_dir = MODELS_DIR / safe_name
    model_dir.mkdir(parents=True, exist_ok=True)
    target = model_dir / Path(incoming_filename or "model.bin").name
    with target.open("wb") as handle:
        shutil.copyfileobj(file_obj, handle)
    return make_model_record(
        name=safe_name,
        model_type="LLM",
        filename=target.name,
        path=str(target),
        source="copied_file",
    )


def register_model_path(
    *,
    name: str,
    model_type: str,
    model_path: str,
    runtime: dict[str, Any] | None = None,
    source: str = "server_path",
) -> dict[str, Any]:
    path_obj = Path(model_path)
    if not path_obj.exists() or not path_obj.is_file():
        raise HTTPException(status_code=400, detail="Указанный путь не существует или не является файлом")
    record = make_model_record(
        name=name,
        model_type=model_type,
        filename=path_obj.name,
        path=str(path_obj),
        source=source,
        runtime=runtime,
    )
    return upsert_model(record)


def _merge_runtime(
    model: dict[str, Any],
    settings: dict[str, Any],
    runtime_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    model_runtime = model.get("runtime") if isinstance(model.get("runtime"), dict) else {}
    merged = merge_dict(settings.get("runtime", {}), model_runtime)
    if runtime_override:
        merged = merge_dict(merged, runtime_override)
    return merged


def _int_value(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float_value(value: Any, default: float) -> float:
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return default


def _tensor_split(value: Any) -> list[float] | None:
    if isinstance(value, list):
        items = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        items = [part.strip() for part in text.replace(";", ",").split(",")]
    result = [_float_value(item, -1.0) for item in items if str(item).strip()]
    return [item for item in result if item >= 0] or None


def _split_mode(value: Any) -> int:
    mapping = {"none": 0, "layer": 1, "row": 2}
    if isinstance(value, int):
        return value
    return mapping.get(str(value or "layer").strip().lower(), 1)


def _filter_supported_kwargs(callable_obj: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
    try:
        params = inspect.signature(callable_obj).parameters
    except (TypeError, ValueError):
        return kwargs
    if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in params.values()):
        return kwargs
    return {key: value for key, value in kwargs.items() if key in params}


def _run_command_for_log(command: list[str], timeout: int = 4) -> str:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            **_subprocess_no_window_kwargs(),
        )
        return (result.stdout or result.stderr or "").strip()
    except Exception as exc:
        return f"command failed: {exc}"


def get_gpu_process_snapshot() -> dict[str, Any]:
    nvidia_smi_path = shutil.which("nvidia-smi")
    if not nvidia_smi_path:
        return {"nvidia_smi_found": False, "pid": os.getpid()}
    return {
        "nvidia_smi_found": True,
        "pid": os.getpid(),
        "gpu_state": _run_command_for_log(
            [
                nvidia_smi_path,
                "--query-gpu=name,utilization.gpu,memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ]
        ),
        "compute_apps": _run_command_for_log(
            [
                nvidia_smi_path,
                "--query-compute-apps=pid,process_name,used_memory",
                "--format=csv,noheader,nounits",
            ]
        ),
    }


def get_runtime_summary(model_id: str) -> dict[str, Any]:
    entry = RUNTIMES.get(model_id) or {}
    worker = entry.get("runtime")
    summary = dict(getattr(worker, "runtime", {}))
    summary.update(getattr(worker, "offload_counts", {}))
    if worker is not None:
        # Report observed offload counts, independently of the requested -1 setting.
        for line in worker.stderr.copy():
            match = re.search(r"offloaded (\d+)/(\d+) layers to GPU", line)
            if match:
                summary.update(gpu_offloaded_layers=int(match[1]), model_layers=int(match[2]))
                break
    if summary.get('mode') == 'CUDA' and 'gpu_offloaded_layers' in summary:
        count, total = summary['gpu_offloaded_layers'], summary['model_layers']
        summary['mode'] = 'CPU' if count == 0 else ('CPU + CUDA' if count < total else 'CUDA')
    return {**summary, "mode": summary.get("mode", "loading"),
            "worker_pid": getattr(getattr(worker, "process", None), "pid", None),
            "loaded_at": entry.get("loaded_at"), "last_used_at": entry.get("last_used_at")}


def source_root() -> Path:
    # In normal dev mode PROJECT_ROOT is the project root. In onefile EXE mode
    # PROJECT_ROOT is dist\ next to LocalAIGPP.exe; backend\app is copied there
    # as a portable runtime package.
    if getattr(sys, "frozen", False):
        if (PROJECT_ROOT / "backend" / "app" / "llama_worker.py").exists():
            return PROJECT_ROOT
        if (PROJECT_ROOT.parent / "backend" / "app" / "llama_worker.py").exists():
            return PROJECT_ROOT.parent
    return PROJECT_ROOT


def external_worker_python() -> str:
    env_python = os.getenv("LOCAL_AI_GPP_WORKER_PYTHON", "").strip().strip('"')
    candidates = [
        Path(env_python) if env_python else None,
        source_root() / "worker_runtime" / "python.exe",
        source_root() / "worker_runtime" / "Scripts" / "python.exe",
        PROJECT_ROOT / "worker_runtime" / "python.exe",
        PROJECT_ROOT / "worker_runtime" / "Scripts" / "python.exe",
        source_root() / "backend" / ".venv" / "Scripts" / "python.exe",
        PROJECT_ROOT / "backend" / ".venv" / "Scripts" / "python.exe",
        Path(sys.executable) if not getattr(sys, 'frozen', False) else None,
    ]
    for candidate in candidates:
        if candidate and candidate.exists():
            return str(candidate)
    return ""


def should_use_external_worker() -> bool:
    return True


def get_external_worker_llama_diagnostics() -> dict[str, Any]:
    python = external_worker_python()
    if not python:
        return {}
    script = r"""
import inspect
import json
from pathlib import Path
from backend.app.cuda_runtime import configure_cuda_dlls
configure_cuda_dlls()

result = {
    "package_installed": False,
    "package_version": "",
    "package_path": "",
    "supported_parameters": [],
    "gpu_related_supported": [],
    "supports_gpu_offload": None,
    "system_info": "",
    "gpu_backend_flags": [],
}
try:
    import llama_cpp
    from llama_cpp import Llama

    result["package_installed"] = True
    result["package_version"] = str(getattr(llama_cpp, "__version__", "unknown"))
    result["package_path"] = str(Path(getattr(llama_cpp, "__file__", "")).resolve())
    try:
        params = inspect.signature(Llama).parameters
        result["supported_parameters"] = sorted(params.keys())
    except Exception:
        pass
    gpu_names = {"n_gpu_layers", "main_gpu", "split_mode", "tensor_split", "offload_kqv", "flash_attn", "op_offload", "swa_full"}
    result["gpu_related_supported"] = [name for name in result["supported_parameters"] if name in gpu_names]
    try:
        from llama_cpp import llama_cpp as llama_cpp_lib

        supports_fn = getattr(llama_cpp_lib, "llama_supports_gpu_offload", None)
        result["supports_gpu_offload"] = bool(supports_fn()) if callable(supports_fn) else None
        system_info_fn = getattr(llama_cpp_lib, "llama_print_system_info", None)
        if callable(system_info_fn):
            raw = system_info_fn()
            result["system_info"] = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw or "")
        upper_info = result["system_info"].upper()
        result["gpu_backend_flags"] = [
            token for token in ("CUDA", "VULKAN", "CLBLAST", "METAL", "HIP", "SYCL", "KOMPUTE") if token in upper_info
        ]
    except Exception as exc:
        result["supports_gpu_offload_error"] = str(exc)
except Exception as exc:
    result["error"] = str(exc)
print(json.dumps(result, ensure_ascii=False, default=str))
"""
    try:
        with _clean_subprocess_dll_search_path():
            completed = subprocess.run(
                [python, "-c", script],
                cwd=str(source_root()),
                capture_output=True,
                text=True,
                timeout=12,
                check=False,
                env=_worker_env(),
                **_subprocess_no_window_kwargs(),
            )
        if completed.returncode != 0:
            return {"package_installed": False, "error": (completed.stderr or completed.stdout or "").strip()}
        return json.loads((completed.stdout or "{}").splitlines()[-1])
    except Exception as exc:
        return {"package_installed": False, "error": str(exc)}


def get_runtime_diagnostics() -> dict[str, Any]:
    from backend.app.cuda_runtime import configure_cuda_dlls
    configure_cuda_dlls()
    supported_parameters: list[str] = []
    gpu_related_supported: list[str] = []
    package_installed = False
    package_version = ""
    package_path = ""
    supports_gpu_offload: bool | None = None
    system_info = ""
    gpu_backend_flags: list[str] = []

    try:
        import llama_cpp
        from llama_cpp import Llama

        package_installed = True
        package_version = str(getattr(llama_cpp, "__version__", "unknown"))
        package_path = str(Path(getattr(llama_cpp, "__file__", "")).resolve())
        try:
            params = inspect.signature(Llama).parameters
            supported_parameters = sorted(params.keys())
        except (TypeError, ValueError):
            supported_parameters = []
        gpu_names = {
            "n_gpu_layers",
            "main_gpu",
            "split_mode",
            "tensor_split",
            "offload_kqv",
            "flash_attn",
            "op_offload",
            "swa_full",
        }
        gpu_related_supported = [name for name in supported_parameters if name in gpu_names]
        try:
            from llama_cpp import llama_cpp as llama_cpp_lib

            supports_fn = getattr(llama_cpp_lib, "llama_supports_gpu_offload", None)
            supports_gpu_offload = bool(supports_fn()) if callable(supports_fn) else None
            system_info_fn = getattr(llama_cpp_lib, "llama_print_system_info", None)
            if callable(system_info_fn):
                raw_system_info = system_info_fn()
                if isinstance(raw_system_info, bytes):
                    system_info = raw_system_info.decode("utf-8", "replace")
                else:
                    system_info = str(raw_system_info or "")
            upper_info = system_info.upper()
            gpu_backend_flags = [
                token
                for token in ("CUDA", "VULKAN", "CLBLAST", "METAL", "HIP", "SYCL", "KOMPUTE")
                if token in upper_info
            ]
            if supports_gpu_offload is None and system_info:
                positive = any(
                    f"{token} = 1" in upper_info
                    or f"{token}=1" in upper_info
                    or f"{token} : 1" in upper_info
                    or f"{token}:1" in upper_info
                    for token in ("CUDA", "VULKAN", "CLBLAST", "METAL", "HIP", "SYCL", "KOMPUTE")
                )
                negative = any(
                    f"{token} = 0" in upper_info
                    or f"{token}=0" in upper_info
                    or f"{token} : 0" in upper_info
                    or f"{token}:0" in upper_info
                    for token in ("CUDA", "VULKAN", "CLBLAST", "METAL", "HIP", "SYCL", "KOMPUTE")
                )
                if positive:
                    supports_gpu_offload = True
                elif negative:
                    supports_gpu_offload = False
        except Exception:
            supports_gpu_offload = None
    except Exception:
        package_installed = False

    if should_use_external_worker() and not package_installed:
        external_diag = get_external_worker_llama_diagnostics()
        if external_diag:
            package_installed = bool(external_diag.get("package_installed"))
            package_version = str(external_diag.get("package_version") or "")
            package_path = str(external_diag.get("package_path") or "")
            supported_parameters = list(external_diag.get("supported_parameters") or [])
            gpu_related_supported = list(external_diag.get("gpu_related_supported") or [])
            supports_gpu_offload = external_diag.get("supports_gpu_offload")  # type: ignore[assignment]
            system_info = str(external_diag.get("system_info") or "")
            gpu_backend_flags = list(external_diag.get("gpu_backend_flags") or [])

    nvidia_smi = ""
    nvidia_smi_found = False
    nvidia_smi_path = shutil.which("nvidia-smi")
    if nvidia_smi_path:
        nvidia_smi_found = True
        try:
            result = subprocess.run(
                [
                    nvidia_smi_path,
                    "--query-gpu=name,driver_version,memory.total",
                    "--format=csv,noheader",
                ],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
                **_subprocess_no_window_kwargs(),
            )
            nvidia_smi = (result.stdout or result.stderr or "").strip()
        except Exception as exc:
            nvidia_smi = f"nvidia-smi найден, но не ответил: {exc}"

    recommendations: list[str] = []
    if not package_installed:
        summary = "llama-cpp-python не установлен в backend runtime."
        recommendations.append("Установи llama-cpp-python в backend\\.venv, затем перезапусти EXE/backend.")
    elif supports_gpu_offload is False:
        summary = "Установлена CPU-сборка llama-cpp-python: параметры GPU принимаются, но видеокарта не используется."
        recommendations.append("Для NVIDIA поставь CUDA-сборку llama-cpp-python и перезапусти приложение.")
        recommendations.append("Быстрый путь: закрой EXE/backend и запусти tools\\06_install_cuda_runtime.bat cu124 0.3.4. Для отката есть tools\\07_install_cpu_runtime.bat.")
        if platform.system() == "Windows":
            recommendations.append("Для cu124 на Windows нужны CUDA 12 runtime DLL в PATH: cudart64_12.dll, cublas64_12.dll, cublasLt64_12.dll.")
            recommendations.append("На Windows CUDA wheel может не найтись автоматически; тогда нужны Visual Studio Build Tools с компонентом Desktop development with C++ и NVIDIA CUDA Toolkit.")
            recommendations.append("Ручная сборка: set CMAKE_ARGS=-DGGML_CUDA=on && set FORCE_CMAKE=1 && backend\\.venv\\Scripts\\python.exe -m pip install --force-reinstall --no-cache-dir llama-cpp-python==0.3.19")
    elif supports_gpu_offload is True:
        summary = "Текущая сборка llama-cpp-python сообщает поддержку GPU offload."
        recommendations.append("Поставь GPU layers = -1, выгрузи модель и снова нажми Прогреть.")
    else:
        summary = "llama-cpp-python установлен, но поддержку GPU offload не удалось определить автоматически."
        recommendations.append("Включи verbose_runtime, выгрузи модель и смотри лог запуска backend.")
        if system_info:
            recommendations.append("llama_print_system_info получен, но явного флага CUDA/Vulkan не найдено.")

    if nvidia_smi_found:
        recommendations.append("NVIDIA GPU обнаружена через nvidia-smi. Для неё обычно нужна CUDA-сборка.")
    else:
        recommendations.append("nvidia-smi не найден. Для NVIDIA установи драйвер; для AMD/Intel пробуй Vulkan-сборку.")

    return {
        "python": sys.executable,
        "platform": platform.platform(),
        "package_installed": package_installed,
        "package_version": package_version,
        "package_path": package_path,
        "nvidia_smi_found": nvidia_smi_found,
        "nvidia_smi": nvidia_smi,
        "supported_parameters": supported_parameters,
        "gpu_related_supported": gpu_related_supported,
        "supports_gpu_offload": supports_gpu_offload,
        "system_info": system_info,
        "gpu_backend_flags": gpu_backend_flags,
        "likely_cpu_build": supports_gpu_offload is False or not package_installed,
        "summary": summary,
        "recommendations": recommendations,
        "install_commands": {
            "cuda": r"tools\06_install_cuda_runtime.bat cu124 0.3.36",
            "vulkan": r"set CMAKE_ARGS=-DGGML_VULKAN=on && backend\.venv\Scripts\python.exe -m pip install --force-reinstall --no-cache-dir llama-cpp-python",
            "cpu": r"backend\.venv\Scripts\python.exe -m pip install --force-reinstall --no-cache-dir llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu",
        },
    }


@contextlib.contextmanager
def runtime_operation():
    if not RUNTIME_LOCK.acquire(blocking=False):
        raise HTTPException(409, "Inference is busy. Retry after the active request finishes.")
    try:
        yield
    finally:
        RUNTIME_LOCK.release()


def _worker_payload(model, cfg, **extra):
    return {"model": validate_model_record(model), "runtime": cfg, **extra}


def _worker_events(worker, payload, *, loading=False):
    cfg = payload.get("runtime") or {}
    timeout = max(1, _int_value(cfg.get("load_timeout_sec" if loading else "request_timeout_sec"), 120 if loading else 180))
    idle = max(1, _int_value(cfg.get("worker_idle_timeout_sec"), WORKER_IDLE_TIMEOUT_SEC))
    yield from worker.events(payload, timeout=timeout, idle_timeout=idle)


def _get_runtime(model, settings, request_log_path=None, runtime_override=None, prewarm=True):
    from backend.app.worker_client import WorkerClient
    from backend.app.llama_worker import build_llama_kwargs
    model = validate_model_record(model)
    if model.get("type") != "LLM" or not model.get("file_exists"):
        raise HTTPException(400, model.get("validation_error") or "A local GGUF LLM is required.")
    cfg = _merge_runtime(model, settings, runtime_override)
    payload = _worker_payload(model, cfg, operation="load")
    load_config = build_llama_kwargs(payload)
    model_id = model["id"]
    entry = RUNTIMES.get(model_id)
    if entry and (entry["load_config"] != load_config or entry["runtime"].process.poll() is not None):
        _unload_runtime(model_id)
        entry = None
    if entry:
        entry.update(config=cfg, policy=cfg.get("warm_policy", "unload_after_idle"))
        append_request_log(request_log_path, "runtime_cache_hit", get_runtime_summary(model_id))
        return entry["runtime"]
    # One loaded model keeps bounded RAM/VRAM even when dialogs choose different models.
    for other_id in list(RUNTIMES):
        _unload_runtime(other_id)
    python = external_worker_python() or ("" if getattr(sys, "frozen", False) else sys.executable)
    if not python:
        raise HTTPException(503, "Packaged worker_runtime/python.exe is missing.")
    with _clean_subprocess_dll_search_path():
        worker = WorkerClient([python, "-m", "backend.app.llama_worker", "--serve"],
                              cwd=str(source_root()), env=_worker_env(request_log_path),
                              **_subprocess_no_window_kwargs())
    stamp = now_iso()
    entry = {"runtime": worker, "state": "loading", "model_name": model.get("name"),
             "loaded_at": stamp, "last_used_at": stamp, "last_used_ts": time.time(),
             "config": cfg, "load_config": load_config, "policy": cfg.get("warm_policy", "unload_after_idle")}
    RUNTIMES[model_id] = entry
    if not prewarm:
        return worker
    try:
        for event in _worker_events(worker, payload, loading=True):
            if event.get("type") == "error":
                raise HTTPException(event.get("status_code", 500), event.get("message"))
            if event.get("type") == "ready":
                entry.update(state="hot", fallback_reason=worker.runtime.get("fallback_reason", ""))
                append_request_log(request_log_path, "runtime_load_success", worker.runtime)
                return worker
        raise HTTPException(502, "Worker did not confirm model loading.")
    except BaseException:
        _unload_runtime(model_id)
        raise

def get_runtime(model, settings, request_log_path=None, runtime_override=None):
    with runtime_operation():
        return _get_runtime(model, settings, request_log_path, runtime_override)


def _completion_events(*, model_id, messages, temperature, max_tokens, stream,
                       request_log_path=None, runtime_override=None, truncate_history=False):
    with runtime_operation():
        _, model, _ = find_model(model_id)
        settings = load_settings()
        cfg = _merge_runtime(model, settings, runtime_override)
        worker = _get_runtime(model, settings, request_log_path, runtime_override, prewarm=False)
        entry = RUNTIMES[model_id]
        entry.update(state="generating", last_used_at=now_iso(), last_used_ts=time.time())
        append_request_log(request_log_path, "generation_start", {"message_count": len(messages), "worker_pid": worker.process.pid})
        events = _worker_events(worker, _worker_payload(model, cfg, messages=messages, temperature=temperature,
                                max_tokens=max_tokens, stream=stream, truncate_history=truncate_history))
        try:
            for event in events:
                if event.get("type") == "error":
                    raise HTTPException(event.get("status_code", 500), event.get("message") or "Inference failed.")
                if event.get("type") in {"done", "result"}:
                    append_request_log(request_log_path, "generation_complete", {"type": event["type"], "worker_pid": worker.process.pid})
                yield event
        finally:
            events.close()
            entry.update(state="hot", last_used_at=now_iso(), last_used_ts=time.time())
            if worker.process.poll() is not None:
                RUNTIMES.pop(model_id, None)


def create_chat_completion(*, model_id, messages, temperature, max_tokens, request_log_path=None,
                           runtime_override=None, truncate_history=False):
    for event in _completion_events(model_id=model_id, messages=messages, temperature=temperature,
                                   max_tokens=max_tokens, stream=False, request_log_path=request_log_path,
                                   runtime_override=runtime_override, truncate_history=truncate_history):
        if event.get("type") == "result":
            return event["result"]
    raise HTTPException(502, "Worker did not return a completion.")


def stream_chat_completion(**kwargs):
    try:
        yield from _completion_events(stream=True, **kwargs)
    except HTTPException as exc:
        yield {"type": "error", "status_code": exc.status_code, "message": str(exc.detail)}
    except Exception as exc:
        yield {"type": "error", "status_code": 500, "message": str(exc)}


def prewarm_runtime(model_id, runtime_override=None):
    with runtime_operation():
        models, model, idx = find_model(model_id)
        request_id, log_path = create_request_log(model_id, "prewarm")
        _get_runtime(model, load_settings(), log_path, runtime_override)
        model.update(status="warm", started_at=now_iso(), last_log_path=str(log_path))
        models[idx] = model
        save_models(models)
        return model


def _unload_runtime(model_id):
    entry = RUNTIMES.pop(model_id, None)
    if not entry:
        return False
    entry["runtime"].close()
    return True


def unload_runtime(model_id):
    with runtime_operation():
        return _unload_runtime(model_id)


def get_runtime_status():
    now_ts = time.time()
    return [{"model_id": model_id, "model_name": entry.get("model_name"),
             "state": entry.get("state", "hot"), "loaded_at": entry.get("loaded_at"),
             "last_used_at": entry.get("last_used_at"),
             "idle_seconds": max(0, int(now_ts-entry.get("last_used_ts", now_ts))),
             "policy": entry.get("policy"), **get_runtime_summary(model_id),
             "runtime_mode": get_runtime_summary(model_id)["mode"]}
            for model_id, entry in list(RUNTIMES.items())]


def enforce_idle_runtime_policy():
    if not RUNTIME_LOCK.acquire(blocking=False):
        return
    try:
        for model_id, entry in list(RUNTIMES.items()):
            cfg = entry["config"]
            if entry["runtime"].process.poll() is not None or (
                cfg.get("warm_policy") == "unload_after_idle" and
                time.time()-entry.get("last_used_ts", time.time()) >= int(cfg.get("idle_unload_sec", 1800))
            ):
                _unload_runtime(model_id)
    finally:
        RUNTIME_LOCK.release()


async def fetch_hub_models(settings: dict[str, Any]) -> list[dict[str, Any]]:
    hub = settings.get("hub", {})
    if not hub.get("enabled"):
        return []
    base_url = str(hub.get("base_url", "")).strip().rstrip("/")
    endpoint = str(hub.get("models_endpoint", "/models")).strip() or "/models"
    if not base_url:
        return []
    headers = {}
    if hub.get("token"):
        headers["Authorization"] = f"Bearer {hub['token']}"
    timeout = float(hub.get("timeout_sec", 30))
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(f"{base_url}{endpoint}", headers=headers)
        response.raise_for_status()
        payload = response.json()
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        models = payload.get("models")
        return models if isinstance(models, list) else []
    return []


async def import_from_hub(payload: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    hub = settings.get("hub", {})
    base_url = str(hub.get("base_url", "")).strip().rstrip("/")
    endpoint = str(hub.get("pull_endpoint", "/models/pull")).strip() or "/models/pull"
    if not base_url:
        raise HTTPException(status_code=400, detail="Hub base_url is empty")
    headers = {}
    if hub.get("token"):
        headers["Authorization"] = f"Bearer {hub['token']}"
    timeout = float(hub.get("timeout_sec", 30))
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(f"{base_url}{endpoint}", headers=headers, json=payload)
        response.raise_for_status()
        result = response.json()
    if isinstance(result, dict) and isinstance(result.get("model"), dict):
        model = result["model"]
        local_path = model.get("local_path") or model.get("path")
        if local_path:
            return register_model_path(
                name=model.get("name") or payload.get("name") or "hub_model",
                model_type=model.get("type") or "LLM",
                model_path=str(local_path),
                source="hub_api",
            )
    if isinstance(result, dict) and result.get("local_path"):
        return register_model_path(
            name=result.get("name") or payload.get("name") or "hub_model",
            model_type=result.get("type") or "LLM",
            model_path=str(result["local_path"]),
            source="hub_api",
        )
    if isinstance(result, dict) and result.get("file_url"):
        file_url = str(result["file_url"])
        filename = result.get("filename") or Path(file_url).name or "hub_model.gguf"
        target_name = sanitize_model_name(result.get("name") or payload.get("name") or "hub_model")
        target_dir = MODELS_DIR / target_name
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / filename
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            stream = await client.get(file_url)
            stream.raise_for_status()
            target_path.write_bytes(stream.content)
        return upsert_model(
            make_model_record(
                name=target_name,
                model_type=result.get("type") or "LLM",
                filename=target_path.name,
                path=str(target_path),
                source="hub_file",
            )
        )
    return upsert_model(
        make_model_record(
            name=payload.get("name") or "hub_model",
            model_type="LLM",
            filename=f"{payload.get('model_id', 'remote')}.stub",
            path=f"HUB::{payload.get('model_id', 'remote')}",
            source="hub_stub",
        )
    )

# -----------------------------------------------------------------------------
# Runtime registry helpers
# -----------------------------------------------------------------------------
def unload_all_runtimes() -> int:
    with runtime_operation():
        ids = list(RUNTIMES)
        for model_id in ids:
            _unload_runtime(model_id)
        return len(ids)
