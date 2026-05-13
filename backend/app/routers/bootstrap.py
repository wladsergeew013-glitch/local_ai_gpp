from __future__ import annotations

from fastapi import APIRouter

from backend.app.core import get_logo_url, load_models, load_settings

router = APIRouter(tags=["bootstrap"])


@router.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/api/bootstrap")
def bootstrap() -> dict:
    settings = load_settings()
    settings["branding"]["logo_url"] = get_logo_url()
    return {"models": load_models(), "settings": settings}
