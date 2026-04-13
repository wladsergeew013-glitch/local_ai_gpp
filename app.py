from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).parent
MODELS_DIR = BASE_DIR / "models_storage"
META_FILE = MODELS_DIR / "models.json"

MODELS_DIR.mkdir(parents=True, exist_ok=True)
if not META_FILE.exists():
    META_FILE.write_text("[]", encoding="utf-8")

app = FastAPI(title="GPP Local AI Hub", version="0.1.0")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def _load_models() -> list[dict[str, Any]]:
    return json.loads(META_FILE.read_text(encoding="utf-8"))


def _save_models(data: list[dict[str, Any]]) -> None:
    META_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


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
    safe_name = model_file.filename or "uploaded_model.bin"
    model_folder = MODELS_DIR / model_name
    model_folder.mkdir(parents=True, exist_ok=True)
    model_path = model_folder / safe_name

    with model_path.open("wb") as f:
        shutil.copyfileobj(model_file.file, f)

    models = _load_models()
    record = {
        "id": f"{model_name}:{safe_name}",
        "name": model_name,
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


@app.post("/api/models/{model_id}/start")
def start_model(model_id: str) -> JSONResponse:
    models = _load_models()
    for model in models:
        if model["id"] == model_id:
            model["status"] = "running"
            model["started_at"] = datetime.now(timezone.utc).isoformat()
            _save_models(models)
            return JSONResponse(model)

    raise HTTPException(status_code=404, detail="Model not found")


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
