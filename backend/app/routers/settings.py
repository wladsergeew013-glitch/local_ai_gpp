from __future__ import annotations

from fastapi import APIRouter, File, UploadFile

from backend.app.core import get_logo_url, load_settings, save_settings, upload_logo_file

router = APIRouter(tags=["settings"])


@router.get("/api/settings")
def get_settings() -> dict:
    data = load_settings()
    data["branding"]["logo_url"] = get_logo_url()
    return data


@router.put("/api/settings")
def put_settings(payload: dict) -> dict:
    data = save_settings(payload)
    data["branding"]["logo_url"] = get_logo_url()
    return data


@router.post("/api/settings/logo")
async def post_logo(logo_file: UploadFile = File(...)) -> dict:
    data = load_settings()
    data["branding"]["logo_url"] = upload_logo_file(logo_file.file, logo_file.filename or "logo.png")
    return save_settings(data)
