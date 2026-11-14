"""Versioned, GUI-independent discovery, readiness and owned request control."""

from fastapi import APIRouter, Depends, Header, HTTPException
from backend.app import core
from backend.app.runtime_manager import API_VERSION, runtime_manager
from backend.app.routers.compat import _require_api_key


def require_compat(
    authorization: str | None = Header(None), x_api_key: str | None = Header(None)
):
    settings = core.load_settings()
    _require_api_key(authorization, x_api_key, settings)
    if not settings.get("server", {}).get("openai_compat_enabled", True):
        raise HTTPException(404, "OpenAI-compatible API is disabled")


router = APIRouter(tags=["runtime-contract"], dependencies=[Depends(require_compat)])


@router.get("/v1/runtime/readiness")
def readiness():
    loaded = core.get_runtime_status()
    ready_models = [
        row
        for row in loaded
        if (entry := core.RUNTIMES.get(row["model_id"]))
        and entry.get("model_loaded")
        and entry["runtime"].process.poll() is None
    ]
    admission = runtime_manager.snapshot()
    busy = admission["busy"] or core.RUNTIME_LOCK.locked()
    return {
        "api_version": API_VERSION,
        "ready": bool(ready_models) and not busy,
        "state": "busy" if busy else ("ready" if ready_models else "model_not_loaded"),
        "ready_model_ids": [row["model_id"] for row in ready_models],
        "models": loaded,
        "admission": {**admission, "busy": busy},
    }


@router.get("/v1/models/{model_id:path}/capabilities")
def capabilities(model_id: str):
    _, model, _ = core.find_model(model_id)
    cfg = core._merge_runtime(
        model, core.load_settings(), core.selected_load_profile(model)
    )
    entry = core.RUNTIMES.get(model_id) or {}
    worker = entry.get("runtime")
    actual = core.get_runtime_summary(model_id) if worker is not None else {}
    loaded = (
        entry.get("model_loaded", False)
        and worker is not None
        and worker.process.poll() is None
        and entry.get("model_signature") == core.model_file_signature(model["path"])
    )
    if not loaded:
        actual = {}
    return {
        "api_version": API_VERSION,
        "model_id": model_id,
        "loaded": loaded,
        "context_window": int(cfg["n_ctx"]),
        "max_output_tokens": min(
            int(cfg.get("max_tokens", 1024)), int(cfg["n_ctx"]) - 257
        ),
        "input": ["text"],
        "streaming": True,
        "parallel_tool_calls": True,
        "tools": {
            "supported": loaded
            and actual.get("tool_calling") in {"qwen3-json", "qwen35-xml"},
            "adapter": actual.get("tool_calling"),
            "verification": "loaded_template" if loaded else "requires_load",
        },
        "runtime": actual,
        "admission": runtime_manager.snapshot(),
    }


@router.post("/v1/models/{model_id:path}/load")
def load(model_id: str, payload: dict | None = None):
    runtime = (payload or {}).get("runtime")
    if runtime is not None:
        if not isinstance(runtime, dict):
            raise HTTPException(400, "runtime must be an object")
        core.validate_runtime(runtime)
    core.prewarm_runtime(model_id, runtime_override=runtime)
    if runtime is not None:
        core.save_model_profile(model_id, runtime)
    return capabilities(model_id)


@router.post("/v1/requests/{request_id}/cancel")
def cancel_request(request_id: str, x_local_ai_cancel_token: str = Header("")):
    return runtime_manager.cancel(request_id, x_local_ai_cancel_token)


@router.post("/v1/owners/{owner_id}/cancel")
def cancel_owner(owner_id: str, x_local_ai_owner_token: str = Header("")):
    return runtime_manager.cancel(owner_id, x_local_ai_owner_token, owner=True)
