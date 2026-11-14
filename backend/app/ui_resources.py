"""Static diagnostic UI resources, independent of the optional desktop host."""

from pathlib import Path
import sys
from backend.app.core import PROJECT_ROOT
from backend.app.storage_paths import frontend_path
from fastapi.responses import FileResponse, JSONResponse

FRONTEND_DIST = (
    Path(sys._MEIPASS) / "frontend_dist"
    if getattr(sys, "frozen", False)
    else PROJECT_ROOT / "frontend/dist"
)


def frontend_spa(full_path="index.html"):
    try:
        requested = frontend_path(FRONTEND_DIST, full_path)
        if full_path.startswith(("api/", "v1/", "assets/")):
            return JSONResponse({"detail": "Not found"}, status_code=404)
        selected = (
            requested
            if requested.is_file()
            else frontend_path(FRONTEND_DIST, "index.html")
        )
        if selected.is_file():
            return FileResponse(selected)
    except (ValueError, OSError):
        pass
    return JSONResponse({"detail": "Not found"}, status_code=404)


def install(app):
    if FRONTEND_DIST.exists():
        app.add_api_route("/", frontend_spa, methods=["GET"], include_in_schema=False)
        app.add_api_route(
            "/{full_path:path}", frontend_spa, methods=["GET"], include_in_schema=False
        )
