from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from backend.app.core import LOGS_DIR, PROJECT_ROOT, unload_all_runtimes

router = APIRouter(tags=['system'])


def _is_allowed_open_target(path: Path) -> bool:
    try:
        resolved = path.resolve()
    except Exception:
        return False
    allowed_roots = [LOGS_DIR.resolve(), (PROJECT_ROOT / 'tools' / 'out').resolve()]
    return any(resolved == root or root in resolved.parents for root in allowed_roots)


@router.post('/api/runtime/unload-all')
def unload_all() -> dict[str, int]:
    return {'unloaded': unload_all_runtimes()}


@router.post('/api/logs/open')
def open_logs(payload: dict[str, Any] | None = None) -> dict[str, str]:
    raw_path = str((payload or {}).get('path') or '').strip()
    target = Path(raw_path) if raw_path else LOGS_DIR
    if not target.exists():
        raise HTTPException(status_code=404, detail=f'Log path not found: {target}')
    if not _is_allowed_open_target(target):
        raise HTTPException(status_code=400, detail='Only logs and tools\\out paths can be opened.')
    if os.name == 'nt':
        os.startfile(str(target))  # type: ignore[attr-defined]
        return {'opened': str(target)}
    return {'path': str(target)}
