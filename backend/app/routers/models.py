from __future__ import annotations

import json

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from backend.app.core import (
    copy_uploaded_model,
    delete_model,
    fetch_hub_models,
    find_model,
    get_runtime_status,
    get_runtime_diagnostics,
    import_from_hub,
    load_models,
    load_settings,
    now_iso,
    prewarm_runtime,
    register_model_path,
    save_models,
    unload_runtime,
    upsert_model,
    validate_model_registry,
)

router = APIRouter(tags=["models"])


@router.get("/api/models")
def list_models() -> list[dict]:
    return load_models()


@router.post("/api/models/validate")
def validate_models() -> list[dict]:
    return validate_model_registry(save=True)


@router.post("/api/models/upload")
async def upload_model(
    model_name: str = Form(...),
    model_type: str = Form("LLM"),
    copy_to_storage: bool = Form(True),
    model_file: UploadFile = File(...),
) -> dict:
    if not copy_to_storage:
        raise HTTPException(
            status_code=400,
            detail=(
                "Файл из браузера всегда копируется в storage. "
                "Если модель уже лежит на сервере, используй регистрацию пути."
            ),
        )
    record = copy_uploaded_model(model_name, model_file.filename or "model.bin", model_file.file)
    record["type"] = model_type
    record["copy_to_storage"] = True
    record["source"] = "upload"
    return upsert_model(record)


@router.post("/api/models/register-path")
def register_path(
    model_name: str = Form(...),
    model_type: str = Form("LLM"),
    model_path: str = Form(...),
    runtime_json: str = Form("{}"),
) -> dict:
    runtime = json.loads(runtime_json or "{}")
    return register_model_path(
        name=model_name,
        model_type=model_type,
        model_path=model_path,
        runtime=runtime if isinstance(runtime, dict) else {},
    )


@router.delete("/api/models/{model_id}")
def remove_model(model_id: str) -> dict[str, bool]:
    delete_model(model_id)
    return {"deleted": True}


@router.post("/api/models/{model_id}/start")
def start_model(model_id: str, payload: dict | None = None) -> dict:
    runtime = payload.get("runtime") if isinstance(payload, dict) and isinstance(payload.get("runtime"), dict) else None
    prewarm_runtime(model_id, runtime_override=runtime)
    models, model, idx = find_model(model_id)
    model["status"] = "running"
    model["started_at"] = now_iso()
    models[idx] = model
    save_models(models)
    return model


@router.post("/api/models/{model_id}/prewarm")
def prewarm_model(model_id: str, payload: dict | None = None) -> dict:
    runtime = payload.get("runtime") if isinstance(payload, dict) and isinstance(payload.get("runtime"), dict) else None
    return prewarm_runtime(model_id, runtime_override=runtime)


@router.post("/api/models/{model_id}/unload")
def unload_model(model_id: str) -> dict:
    models, model, idx = find_model(model_id)
    unloaded = unload_runtime(model_id)
    model["status"] = "saved"
    models[idx] = model
    save_models(models)
    return {"model_id": model_id, "unloaded": unloaded}


@router.get("/api/runtime/status")
def runtime_status() -> list[dict]:
    return get_runtime_status()


@router.get("/api/runtime/diagnostics")
def runtime_diagnostics() -> dict:
    return get_runtime_diagnostics()


@router.get("/api/hub/models")
async def list_hub_models() -> list[dict]:
    settings = load_settings()
    return await fetch_hub_models(settings)


@router.post("/api/hub/import")
async def import_model_from_hub(payload: dict) -> dict:
    settings = load_settings()
    return await import_from_hub(payload, settings)
