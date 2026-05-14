from __future__ import annotations

import json
import contextlib
import inspect
import os
import platform
import queue
import re
import subprocess
import shutil
import sys
import threading
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import HTTPException

APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else APP_DIR.parent.parent
MODELS_DIR = PROJECT_ROOT / "models_storage"
LOGS_DIR = PROJECT_ROOT / "logs"
BRANDING_DIR = MODELS_DIR / "branding"
MODELS_FILE = MODELS_DIR / "models.json"
SETTINGS_FILE = MODELS_DIR / "settings.json"

MODELS_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)
BRANDING_DIR.mkdir(parents=True, exist_ok=True)

RUNTIMES: dict[str, dict[str, Any]] = {}
WORKER_FIRST_EVENT_TIMEOUT_SEC = 30
WORKER_IDLE_TIMEOUT_SEC = 900


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
        "warm_policy": "keep_hot",
        "idle_unload_sec": 1800,
        "preload_on_start": False,
    },
    "server": {
        "host": "127.0.0.1",
        "port": 8000,
        "public_base_url": "http://127.0.0.1:8000",
        "openai_compat_enabled": True,
        "openai_compat_path": "/v1/chat/completions",
        "cors_origins": [
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
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        _write_json(path, fallback)
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        _write_json(path, fallback)
        return fallback


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
    _write_json(SETTINGS_FILE, merged)
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
        candidates.append(PROJECT_ROOT / path_obj)
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
    _write_json(MODELS_FILE, models)


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
    models = [m for m in load_models() if m.get("id") != checked.get("id")]
    models.insert(0, checked)
    save_models(models)
    return checked


def delete_model(model_id: str) -> None:
    models = load_models()
    next_models = [m for m in models if m.get("id") != model_id]
    if len(next_models) == len(models):
        raise HTTPException(status_code=404, detail="Model not found")
    save_models(next_models)
    unload_runtime(model_id)


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
    config = entry.get("config") if isinstance(entry.get("config"), dict) else {}
    n_gpu_layers = _int_value(config.get("n_gpu_layers"), 0)
    if entry.get("fallback_reason"):
        mode = "CPU fallback"
    elif n_gpu_layers == 0:
        mode = "CPU only"
    elif n_gpu_layers < 0:
        mode = "GPU requested: all layers"
    else:
        mode = f"GPU requested: {n_gpu_layers} layers"
    return {
        "mode": mode,
        "n_gpu_layers": n_gpu_layers,
        "fallback_reason": entry.get("fallback_reason", ""),
        "loaded_at": entry.get("loaded_at"),
        "last_used_at": entry.get("last_used_at"),
    }


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
    ]
    for candidate in candidates:
        if candidate and candidate.exists():
            return str(candidate)
    return ""


def should_use_external_worker() -> bool:
    return bool(getattr(sys, "frozen", False) and external_worker_python())


def worker_payload(
    *,
    model: dict[str, Any],
    runtime_cfg: dict[str, Any],
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int,
    stream: bool,
) -> dict[str, Any]:
    return {
        "model": validate_model_record(model),
        "runtime": runtime_cfg,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": stream,
    }


def run_worker_completion(payload: dict[str, Any], request_log_path: Path | None = None) -> dict[str, Any]:
    python = external_worker_python()
    if not python:
        raise HTTPException(status_code=500, detail="External llama.cpp worker python не найден")
    append_request_log(request_log_path, "external_worker_start", {"python": python, "cwd": str(source_root())})
    with _clean_subprocess_dll_search_path():
        process = subprocess.Popen(
            [python, "-m", "backend.app.llama_worker"],
            cwd=str(source_root()),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_worker_env(request_log_path),
            **_subprocess_no_window_kwargs(),
        )
    stdout_bytes, stderr_bytes = process.communicate(json.dumps(payload, ensure_ascii=False).encode("utf-8"), timeout=None)
    stdout = stdout_bytes.decode("utf-8", "replace") if stdout_bytes else ""
    stderr = stderr_bytes.decode("utf-8", "replace") if stderr_bytes else ""
    append_request_log(request_log_path, "external_worker_output", {"returncode": process.returncode, "stdout": stdout, "stderr": stderr})
    result_payload: dict[str, Any] | None = None
    runtime_payload: dict[str, Any] = {}
    error_payload: dict[str, Any] | None = None
    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if item.get("type") == "runtime":
            runtime_payload = item
        elif item.get("type") == "result":
            result_payload = item.get("result") if isinstance(item.get("result"), dict) else {}
        elif item.get("type") == "error":
            error_payload = item
    if result_payload is None:
        message = (error_payload or {}).get("message") or stderr or "External llama.cpp worker не вернул результат"
        raise HTTPException(status_code=500, detail=f"{message}. Лог выполнения: {request_log_path}")
    result_payload["_local_ai_gpp"] = {
        "runtime": {
            "mode": runtime_payload.get("mode", "external worker"),
            "n_gpu_layers": payload.get("runtime", {}).get("n_gpu_layers"),
            "worker_python": python,
        },
        "gpu_snapshot_after_generation": get_gpu_process_snapshot(),
    }
    return result_payload


def stream_worker_completion(payload: dict[str, Any], request_log_path: Path | None = None):
    python = external_worker_python()
    if not python:
        yield {"type": "error", "message": "External llama.cpp worker python не найден"}
        return
    append_request_log(
        request_log_path,
        "external_worker_stream_start",
        {"python": python, "cwd": str(source_root()), "gpu_snapshot_before_worker": get_gpu_process_snapshot()},
    )
    with _clean_subprocess_dll_search_path():
        process = subprocess.Popen(
            [python, "-m", "backend.app.llama_worker"],
            cwd=str(source_root()),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=1,
            env=_worker_env(request_log_path),
            **_subprocess_no_window_kwargs(),
        )
    assert process.stdin is not None
    process.stdin.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    process.stdin.close()

    output_queue: queue.Queue[tuple[str, Any]] = queue.Queue()

    def read_worker_output() -> None:
        try:
            assert process.stdout is not None
            for raw_line in process.stdout:
                output_queue.put(("line", raw_line))
            stderr = process.stderr.read().decode("utf-8", "replace") if process.stderr else ""
            returncode = process.wait()
            output_queue.put(("end", {"returncode": returncode, "stderr": stderr}))
        except Exception as exc:
            output_queue.put(("reader_error", {"message": str(exc), "traceback": traceback.format_exc()}))

    threading.Thread(target=read_worker_output, daemon=True).start()

    started = time.monotonic()
    last_activity = started
    seen_events = 0
    while True:
        try:
            kind, data = output_queue.get(timeout=1)
        except queue.Empty:
            now = time.monotonic()
            if seen_events == 0 and now - started > WORKER_FIRST_EVENT_TIMEOUT_SEC:
                process.kill()
                try:
                    process.wait(timeout=5)
                except Exception:
                    pass
                append_request_log(
                    request_log_path,
                    "external_worker_first_event_timeout",
                    {
                        "timeout_sec": WORKER_FIRST_EVENT_TIMEOUT_SEC,
                        "returncode": process.poll(),
                        "gpu_snapshot_after_timeout": get_gpu_process_snapshot(),
                    },
                )
                yield {
                    "type": "error",
                    "message": (
                        "llama.cpp worker не отдал первое событие запуска. "
                        "Запрос остановлен, чтобы интерфейс не висел бесконечно. "
                        "Закрой старые экземпляры EXE и попробуй снова; подробности в логе. "
                        f"Лог выполнения: {request_log_path}"
                    ),
                }
                return
            if seen_events > 0 and now - last_activity > WORKER_IDLE_TIMEOUT_SEC:
                process.kill()
                try:
                    process.wait(timeout=5)
                except Exception:
                    pass
                append_request_log(
                    request_log_path,
                    "external_worker_idle_timeout",
                    {
                        "timeout_sec": WORKER_IDLE_TIMEOUT_SEC,
                        "seen_events": seen_events,
                        "gpu_snapshot_after_timeout": get_gpu_process_snapshot(),
                    },
                )
                yield {"type": "error", "message": "llama.cpp worker слишком долго не отдавал данные. Запрос остановлен."}
                return
            continue

        if kind == "line":
            seen_events += 1
            last_activity = time.monotonic()
            line = data.decode("utf-8", "replace")
            if not line.strip():
                continue
            append_request_log(request_log_path, "external_worker_stream_line", line.strip())
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                yield {"type": "log", "text": line.strip()}
            continue

        if kind == "reader_error":
            append_request_log(request_log_path, "external_worker_reader_error", data)
            yield {"type": "error", "message": data.get("message") or "Ошибка чтения вывода llama.cpp worker"}
            return

        if kind == "end":
            append_request_log(
                request_log_path,
                "external_worker_stream_end",
                {**data, "gpu_snapshot_after_worker": get_gpu_process_snapshot()},
            )
            if data.get("returncode") and data.get("stderr"):
                yield {"type": "error", "message": data["stderr"]}
            return


def get_external_worker_llama_diagnostics() -> dict[str, Any]:
    python = external_worker_python()
    if not python:
        return {}
    script = r"""
import inspect
import json
from pathlib import Path

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
            "cuda": r"tools\06_install_cuda_runtime.bat cu124 0.3.4",
            "vulkan": r"set CMAKE_ARGS=-DGGML_VULKAN=on && backend\.venv\Scripts\python.exe -m pip install --force-reinstall --no-cache-dir llama-cpp-python",
            "cpu": r"backend\.venv\Scripts\python.exe -m pip install --force-reinstall --no-cache-dir llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu",
        },
    }


def get_runtime(
    model: dict[str, Any],
    settings: dict[str, Any],
    request_log_path: Path | None = None,
    runtime_override: dict[str, Any] | None = None,
) -> Any:
    model_id = model["id"]
    runtime_cfg = _merge_runtime(model, settings, runtime_override)
    cached = RUNTIMES.get(model_id)
    if cached is not None:
        if cached.get("config") != runtime_cfg:
            append_request_log(request_log_path, "runtime_config_changed_reloading", {"old": cached.get("config"), "new": runtime_cfg})
            unload_runtime(model_id)
        else:
            cached["state"] = "hot"
            cached["last_used_at"] = now_iso()
            cached["last_used_ts"] = time.time()
            append_request_log(request_log_path, "runtime_cache_hit", get_runtime_summary(model_id))
            return cached["runtime"]
    if model.get("type") != "LLM":
        raise HTTPException(status_code=400, detail="Полноценный runtime поддержан только для LLM")
    try:
        from llama_cpp import Llama
    except ImportError as exc:
        raise HTTPException(status_code=500, detail="llama-cpp-python не установлен") from exc
    model_path = Path(model["path"])
    if not model_path.exists():
        raise HTTPException(status_code=400, detail=f"Файл модели не найден: {model_path}")
    llama_kwargs: dict[str, Any] = {
        "model_path": str(model_path),
        "n_ctx": _int_value(runtime_cfg.get("n_ctx"), 4096),
        "n_batch": _int_value(runtime_cfg.get("n_batch"), 512),
        "n_threads": _int_value(runtime_cfg.get("n_threads"), max(1, os.cpu_count() or 4)),
        "n_threads_batch": _int_value(runtime_cfg.get("n_threads_batch"), 0),
        "n_gpu_layers": _int_value(runtime_cfg.get("n_gpu_layers"), 0),
        "main_gpu": _int_value(runtime_cfg.get("main_gpu"), 0),
        "split_mode": _split_mode(runtime_cfg.get("split_mode")),
        "offload_kqv": bool(runtime_cfg.get("offload_kqv", True)),
        "flash_attn": bool(runtime_cfg.get("flash_attn", False)),
        "op_offload": bool(runtime_cfg.get("op_offload", True)),
        "swa_full": bool(runtime_cfg.get("swa_full", False)),
        "use_mmap": bool(runtime_cfg.get("use_mmap", True)),
        "use_mlock": bool(runtime_cfg.get("use_mlock", False)),
        "verbose": bool(runtime_cfg.get("verbose_runtime", False)),
        "seed": _int_value(runtime_cfg.get("seed"), -1),
    }
    tensor_split = _tensor_split(runtime_cfg.get("tensor_split"))
    if tensor_split:
        llama_kwargs["tensor_split"] = tensor_split
    fallback_reason = ""
    append_request_log(
        request_log_path,
        "runtime_load_primary_attempt",
        {
            "model": validate_model_record(model),
            "runtime_config": runtime_cfg,
            "llama_kwargs": _filter_supported_kwargs(Llama, llama_kwargs),
            "gpu_snapshot_before_load": get_gpu_process_snapshot(),
        },
    )
    try:
        runtime = Llama(**_filter_supported_kwargs(Llama, llama_kwargs))
    except Exception as exc:
        append_request_log(
            request_log_path,
            "runtime_load_primary_failed",
            {
                "error": str(exc),
                "traceback": traceback.format_exc(),
                "gpu_snapshot_after_failure": get_gpu_process_snapshot(),
            },
        )
        if bool(runtime_cfg.get("gpu_fallback_to_cpu", True)):
            fallback_kwargs: dict[str, Any] = {
                "model_path": str(model_path),
                "n_ctx": _int_value(runtime_cfg.get("n_ctx"), 4096),
                "n_batch": min(_int_value(runtime_cfg.get("n_batch"), 512), 256),
                "n_threads": _int_value(runtime_cfg.get("n_threads"), max(1, os.cpu_count() or 4)),
                "n_threads_batch": 0,
                "n_gpu_layers": 0,
                "use_mmap": False,
                "use_mlock": False,
                "verbose": bool(runtime_cfg.get("verbose_runtime", False)),
            }
            append_request_log(
                request_log_path,
                "runtime_load_cpu_fallback_attempt",
                {"llama_kwargs": _filter_supported_kwargs(Llama, fallback_kwargs)},
            )
            try:
                runtime = Llama(**_filter_supported_kwargs(Llama, fallback_kwargs))
                fallback_reason = str(exc)
                runtime_cfg = merge_dict(runtime_cfg, {"n_gpu_layers": 0, "_fallback_reason": fallback_reason})
                append_request_log(
                    request_log_path,
                    "runtime_load_cpu_fallback_success",
                    {"reason": fallback_reason, "gpu_snapshot_after_load": get_gpu_process_snapshot()},
                )
            except Exception as fallback_exc:
                append_request_log(
                    request_log_path,
                    "runtime_load_cpu_fallback_failed",
                    {
                        "primary_error": str(exc),
                        "fallback_error": str(fallback_exc),
                        "traceback": traceback.format_exc(),
                        "gpu_snapshot_after_fallback_failure": get_gpu_process_snapshot(),
                    },
                )
                log_hint = f" Лог выполнения: {request_log_path}" if request_log_path else ""
                raise HTTPException(
                    status_code=500,
                    detail=(
                        f"Не удалось загрузить модель в llama.cpp: {exc}. "
                        f"CPU fallback тоже не поднялся: {fallback_exc}. "
                        "Если менял GPU-настройки, проверь диагностику runtime, выгрузи модель и попробуй CPU preset."
                        f"{log_hint}"
                    ),
                ) from exc
        else:
            log_hint = f" Лог выполнения: {request_log_path}" if request_log_path else ""
            raise HTTPException(
                status_code=500,
                detail=(
                    f"Не удалось загрузить модель в llama.cpp: {exc}. "
                    "Если менял GPU-настройки, проверь диагностику runtime, выгрузи модель и попробуй CPU/Hybrid/GPU preset."
                    f"{log_hint}"
                ),
            ) from exc
    loaded_at = now_iso()
    RUNTIMES[model_id] = {
        "runtime": runtime,
        "state": "hot",
        "model_name": model.get("name") or model_id,
        "loaded_at": loaded_at,
        "last_used_at": loaded_at,
        "last_used_ts": time.time(),
        "config": runtime_cfg,
        "policy": runtime_cfg.get("warm_policy", "keep_hot"),
        "fallback_reason": fallback_reason,
    }
    append_request_log(
        request_log_path,
        "runtime_load_success",
        {
            "runtime": get_runtime_summary(model_id),
            "gpu_snapshot_after_load": get_gpu_process_snapshot(),
        },
    )
    return runtime


def create_chat_completion(
    *,
    model_id: str,
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int,
    request_log_path: Path | None = None,
    runtime_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _, model, _ = find_model(model_id)
    if str(model.get("path", "")).startswith("HUB::"):
        raise HTTPException(status_code=400, detail="Модель из хаба еще не локализирована. Импортируй ее в движок.")
    settings = load_settings()
    runtime_cfg = _merge_runtime(model, settings, runtime_override)
    append_request_log(
        request_log_path,
        "chat_completion_start",
        {
            "model": validate_model_record(model),
            "runtime_config": runtime_cfg,
            "message_count": len(messages),
            "messages": [
                {"role": item.get("role"), "content": str(item.get("content") or "")[:4000]}
                for item in messages
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
        },
    )
    if should_use_external_worker():
        return run_worker_completion(
            worker_payload(
                model=model,
                runtime_cfg=runtime_cfg,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=False,
            ),
            request_log_path=request_log_path,
        )
    runtime = get_runtime(model, settings, request_log_path=request_log_path, runtime_override=runtime_override)
    chat_kwargs: dict[str, Any] = {
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "top_k": _int_value(runtime_cfg.get("top_k"), 40),
        "top_p": _float_value(runtime_cfg.get("top_p"), 0.95),
        "min_p": _float_value(runtime_cfg.get("min_p"), 0.05),
        "repeat_penalty": _float_value(runtime_cfg.get("repeat_penalty"), 1.1),
        "seed": _int_value(runtime_cfg.get("seed"), -1),
    }
    try:
        started = time.perf_counter()
        result = runtime.create_chat_completion(**_filter_supported_kwargs(runtime.create_chat_completion, chat_kwargs))
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        runtime_summary = get_runtime_summary(model_id)
        result["_local_ai_gpp"] = {
            "runtime": runtime_summary,
            "generation_elapsed_ms": elapsed_ms,
            "gpu_snapshot_after_generation": get_gpu_process_snapshot(),
        }
        append_request_log(
            request_log_path,
            "chat_completion_success",
            {
                "runtime": runtime_summary,
                "usage": result.get("usage", {}),
                "finish_reason": ((result.get("choices") or [{}])[0] or {}).get("finish_reason"),
                "generation_elapsed_ms": elapsed_ms,
                "gpu_snapshot_after_generation": result["_local_ai_gpp"]["gpu_snapshot_after_generation"],
            },
        )
        return result
    except Exception as exc:
        append_request_log(
            request_log_path,
            "chat_completion_failed",
            {"error": str(exc), "traceback": traceback.format_exc(), "gpu_snapshot_after_error": get_gpu_process_snapshot()},
        )
        raise HTTPException(status_code=500, detail=f"Ошибка генерации llama.cpp: {exc}") from exc


def stream_chat_completion(
    *,
    model_id: str,
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int,
    request_log_path: Path | None = None,
    runtime_override: dict[str, Any] | None = None,
):
    _, model, _ = find_model(model_id)
    if str(model.get("path", "")).startswith("HUB::"):
        yield {"type": "error", "message": "Модель из хаба еще не локализирована. Импортируй ее в движок."}
        return
    settings = load_settings()
    runtime_cfg = _merge_runtime(model, settings, runtime_override)
    append_request_log(
        request_log_path,
        "stream_chat_start",
        {
            "model": validate_model_record(model),
            "runtime_config": runtime_cfg,
            "message_count": len(messages),
            "messages": [
                {"role": item.get("role"), "content": str(item.get("content") or "")[:4000]}
                for item in messages
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "external_worker": should_use_external_worker(),
        },
    )
    if should_use_external_worker():
        yield from stream_worker_completion(
            worker_payload(
                model=model,
                runtime_cfg=runtime_cfg,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
            ),
            request_log_path=request_log_path,
        )
        return

    try:
        runtime = get_runtime(model, settings, request_log_path=request_log_path, runtime_override=runtime_override)
        runtime_summary = get_runtime_summary(model_id)
        yield {"type": "runtime", "mode": runtime_summary.get("mode"), "runtime": runtime_summary}
        chat_kwargs: dict[str, Any] = {
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "top_k": _int_value(runtime_cfg.get("top_k"), 40),
            "top_p": _float_value(runtime_cfg.get("top_p"), 0.95),
            "min_p": _float_value(runtime_cfg.get("min_p"), 0.05),
            "repeat_penalty": _float_value(runtime_cfg.get("repeat_penalty"), 1.1),
            "seed": _int_value(runtime_cfg.get("seed"), -1),
            "stream": True,
        }
        full_text = ""
        finish_reason = None
        started = time.perf_counter()
        for chunk in runtime.create_chat_completion(**_filter_supported_kwargs(runtime.create_chat_completion, chat_kwargs)):
            choice = (chunk.get("choices") or [{}])[0]
            finish_reason = choice.get("finish_reason") or finish_reason
            delta = choice.get("delta") or {}
            text = str(delta.get("content") or "")
            if text:
                full_text += text
                yield {"type": "delta", "text": text}
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        append_request_log(
            request_log_path,
            "stream_chat_success",
            {
                "finish_reason": finish_reason,
                "elapsed_ms": elapsed_ms,
                "content_chars": len(full_text),
                "runtime": runtime_summary,
                "gpu_snapshot_after_generation": get_gpu_process_snapshot(),
            },
        )
        yield {"type": "done", "content": full_text, "finish_reason": finish_reason, "elapsed_ms": elapsed_ms, "usage": {}, "runtime": runtime_summary}
    except Exception as exc:
        append_request_log(
            request_log_path,
            "stream_chat_failed",
            {"error": str(exc), "traceback": traceback.format_exc(), "gpu_snapshot_after_error": get_gpu_process_snapshot()},
        )
        yield {"type": "error", "message": str(exc)}


def prewarm_runtime(model_id: str, runtime_override: dict[str, Any] | None = None) -> dict[str, Any]:
    models, model, idx = find_model(model_id)
    settings = load_settings()
    request_id, log_path = create_request_log(model_id, "prewarm")
    if model.get("type") == "LLM" and not str(model.get("path", "")).startswith("HUB::"):
        try:
            get_runtime(model, settings, request_log_path=log_path, runtime_override=runtime_override)
        except HTTPException as exc:
            append_request_log(log_path, "prewarm_failed", {"error": exc.detail, "request_id": request_id})
            detail = exc.detail if isinstance(exc.detail, str) else json.dumps(exc.detail, ensure_ascii=False)
            raise HTTPException(status_code=exc.status_code, detail=f"{detail} Лог выполнения: {log_path}") from exc
    model["status"] = "warm"
    model["started_at"] = now_iso()
    model["last_log_path"] = str(log_path)
    models[idx] = model
    save_models(models)
    append_request_log(log_path, "prewarm_success", {"request_id": request_id, "model_id": model_id})
    return model


def unload_runtime(model_id: str) -> bool:
    entry = RUNTIMES.pop(model_id, None)
    if not entry:
        return False
    runtime = entry.get("runtime")
    close = getattr(runtime, "close", None)
    if callable(close):
        close()
    return True


def get_runtime_status() -> list[dict[str, Any]]:
    now_ts = time.time()
    models_by_id = {str(item.get("id")): item for item in load_models()}
    rows: list[dict[str, Any]] = []
    for model_id, entry in RUNTIMES.items():
        model = models_by_id.get(model_id, {})
        last_used_ts = float(entry.get("last_used_ts") or now_ts)
        rows.append(
            {
                "model_id": model_id,
                "model_name": entry.get("model_name") or model.get("name") or model_id,
                "state": entry.get("state", "hot"),
                "loaded_at": entry.get("loaded_at"),
                "last_used_at": entry.get("last_used_at"),
                "idle_seconds": max(0, int(now_ts - last_used_ts)),
                "policy": entry.get("policy", "keep_hot"),
                "runtime_mode": get_runtime_summary(model_id).get("mode"),
                "fallback_reason": entry.get("fallback_reason", ""),
            }
        )
    return rows


def enforce_idle_runtime_policy() -> None:
    settings = load_settings()
    runtime_settings = settings.get("runtime", {})
    if runtime_settings.get("warm_policy") != "unload_after_idle":
        return
    idle_limit = int(runtime_settings.get("idle_unload_sec", 1800) or 1800)
    now_ts = time.time()
    for model_id, entry in list(RUNTIMES.items()):
        last_used_ts = float(entry.get("last_used_ts") or now_ts)
        if now_ts - last_used_ts >= idle_limit:
            unload_runtime(model_id)


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
    count = 0
    for model_id in list(RUNTIMES.keys()):
        try:
            if unload_runtime(model_id):
                count += 1
        except Exception:
            RUNTIMES.pop(model_id, None)
    return count