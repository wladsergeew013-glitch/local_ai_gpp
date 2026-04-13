from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

APP_DIR = Path(__file__).parent
PROJECT_ROOT = APP_DIR.parent.parent
MODELS_DIR = PROJECT_ROOT / "models_storage"
META_FILE = MODELS_DIR / "models.json"

MODELS_DIR.mkdir(parents=True, exist_ok=True)
if not META_FILE.exists():
    META_FILE.write_text("[]", encoding="utf-8")

app = FastAPI(title="GPP Local AI Hub", version="0.3.0")
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))

MODEL_RUNTIMES: dict[str, Any] = {}


class ChatRequest(BaseModel):
    model_id: str
    message: str = Field(min_length=1)
    system_prompt: str = ""
    max_tokens: int = Field(default=256, ge=16, le=4096)
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)


def _sanitize_segment(value: str, field_name: str, *, allow_dot: bool = True) -> str:
    candidate = value.strip()
    if not candidate:
        raise HTTPException(status_code=400, detail=f"{field_name} is required")

    if any(sep in candidate for sep in ("/", "\\", os.sep)):
        raise HTTPException(status_code=400, detail=f"{field_name} must not contain path separators")

    if candidate in {".", ".."} or ".." in candidate:
        raise HTTPException(status_code=400, detail=f"{field_name} must not contain relative path segments")

    pattern = r"^[A-Za-z0-9._ -]+$" if allow_dot else r"^[A-Za-z0-9_ -]+$"
    if not re.match(pattern, candidate):
        raise HTTPException(status_code=400, detail=f"{field_name} contains unsupported characters")

    return candidate


def _resolve_model_file_path(model_name: str, filename: str) -> tuple[str, str, Path]:
    safe_model_name = _sanitize_segment(model_name, "model_name", allow_dot=False)
    _sanitize_segment(filename, "model_file.filename")
    safe_filename = _sanitize_segment(Path(filename).name, "model_file.filename")

    model_folder = (MODELS_DIR / safe_model_name).resolve()
    model_folder.mkdir(parents=True, exist_ok=True)

    model_path = (model_folder / safe_filename).resolve()
    if model_path.parent != model_folder:
        raise HTTPException(status_code=400, detail="Invalid model path")

    if MODELS_DIR.resolve() not in model_path.parents:
        raise HTTPException(status_code=400, detail="Resolved model path escapes storage directory")

    return safe_model_name, safe_filename, model_path


def _load_models() -> list[dict[str, Any]]:
    return json.loads(META_FILE.read_text(encoding="utf-8"))


def _save_models(data: list[dict[str, Any]]) -> None:
    META_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _find_model_or_404(model_id: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    models = _load_models()
    for model in models:
        if model["id"] == model_id:
            return models, model
    raise HTTPException(status_code=404, detail="Model not found")


def _load_llama_runtime(model: dict[str, Any], n_ctx: int = 4096) -> Any:
    if model["id"] in MODEL_RUNTIMES:
        return MODEL_RUNTIMES[model["id"]]

    try:
        from llama_cpp import Llama
    except ImportError as exc:
        raise HTTPException(
            status_code=500,
            detail="llama-cpp-python не установлен. Установите зависимости из requirements.txt",
        ) from exc

    if model["type"] != "LLM":
        raise HTTPException(status_code=400, detail="Полноценный запуск поддержан только для LLM")

    model_path = Path(model["path"])
    if not model_path.exists():
        raise HTTPException(status_code=400, detail=f"Файл модели не найден: {model_path}")

    runtime = Llama(model_path=str(model_path), n_ctx=n_ctx, verbose=False)
    MODEL_RUNTIMES[model["id"]] = runtime
    return runtime


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("index.html", {"request": request, "models": _load_models()})


@app.get("/api/models")
def list_models() -> JSONResponse:
    return JSONResponse(_load_models())


@app.post("/api/models/upload")
async def upload_model(
    model_name: str = Form(...),
    model_type: str = Form(...),
    model_file: UploadFile = File(...),
) -> JSONResponse:
    incoming_filename = model_file.filename or "uploaded_model.bin"
    safe_model_name, safe_name, model_path = _resolve_model_file_path(model_name, incoming_filename)

    with model_path.open("wb") as f:
        shutil.copyfileobj(model_file.file, f)

    models = _load_models()
    record = {
        "id": f"{safe_model_name}:{safe_name}",
        "name": safe_model_name,
        "type": model_type,
        "filename": safe_name,
        "path": str(model_path),
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
        "status": "saved",
    }

    models = [m for m in models if m["id"] != record["id"]]
    models.append(record)
    _save_models(models)
    return JSONResponse(record)


@app.post("/api/models/register-path")
def register_model_path(
    model_name: str = Form(...),
    model_type: str = Form(...),
    model_path: str = Form(...),
) -> JSONResponse:
    path_obj = Path(model_path)
    if not path_obj.exists() or not path_obj.is_file():
        raise HTTPException(status_code=400, detail="Указанный путь не существует или не файл")

    models = _load_models()
    record = {
        "id": f"{model_name}:{path_obj.name}",
        "name": model_name,
        "type": model_type,
        "filename": path_obj.name,
        "path": str(path_obj),
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
        "status": "saved",
        "source": "server_path",
    }

    models = [m for m in models if m["id"] != record["id"]]
    models.append(record)
    _save_models(models)
    return JSONResponse(record)


@app.post("/api/models/{model_id}/start")
def start_model(model_id: str) -> JSONResponse:
    models, model = _find_model_or_404(model_id)

    if model["type"] == "LLM":
        _load_llama_runtime(model)

    model["status"] = "running"
    model["started_at"] = datetime.now(timezone.utc).isoformat()
    _save_models(models)
    return JSONResponse(model)


@app.post("/api/chat")
def chat_with_llm(payload: ChatRequest) -> JSONResponse:
    _, model = _find_model_or_404(payload.model_id)
    runtime = _load_llama_runtime(model)

    messages: list[dict[str, str]] = []
    if payload.system_prompt.strip():
        messages.append({"role": "system", "content": payload.system_prompt.strip()})
    messages.append({"role": "user", "content": payload.message.strip()})

    result = runtime.create_chat_completion(
        messages=messages,
        max_tokens=payload.max_tokens,
        temperature=payload.temperature,
    )
    answer = result["choices"][0]["message"]["content"]
    return JSONResponse({"answer": answer, "model_id": payload.model_id})


@app.post("/api/train")
def train_placeholder(model_id: str = Form(...), dataset_path: str = Form(...)) -> JSONResponse:
    if not os.path.exists(dataset_path):
        raise HTTPException(status_code=400, detail="Dataset path not found")
    return JSONResponse(
        {
            "message": "Training area placeholder created. Integrate your trainer pipeline here.",
            "model_id": model_id,
            "dataset_path": dataset_path,
            "status": "queued",
        }
    )
