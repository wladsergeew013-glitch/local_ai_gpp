from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.app.core import (
    MODELS_DIR,
    PROJECT_ROOT,
    enforce_idle_runtime_policy,
    load_models,
    load_settings,
    prewarm_runtime,
)
from backend.app.routers.bootstrap import router as bootstrap_router
from backend.app.routers.chat import router as chat_router
from backend.app.routers.compat import router as compat_router
from backend.app.routers.models import router as models_router
from backend.app.routers.settings import router as settings_router
from backend.app.routers.system import router as system_router


def _normalize_origins(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        raw_items = value.replace(',', '\n').splitlines()
        return [item.strip() for item in raw_items if item.strip()]
    return []


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = load_settings()
    runtime_settings = settings.get('runtime', {})
    if runtime_settings.get('preload_on_start'):
        for model in load_models():
            if model.get('type') == 'LLM' and not str(model.get('path', '')).startswith('HUB::'):
                try:
                    prewarm_runtime(str(model['id']))
                except Exception:
                    pass

    stop_event = asyncio.Event()

    async def runtime_maintenance_loop() -> None:
        while not stop_event.is_set():
            try:
                enforce_idle_runtime_policy()
            except Exception:
                pass
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=30)
            except asyncio.TimeoutError:
                pass

    task = asyncio.create_task(runtime_maintenance_loop())
    try:
        yield
    finally:
        stop_event.set()
        await task


settings = load_settings()
server_settings = settings.get('server', {})
env_cors_origins = _normalize_origins(os.getenv('LOCAL_AI_GPP_CORS_ORIGINS', ''))
configured_cors_origins = _normalize_origins(server_settings.get('cors_origins')) or [
    'http://127.0.0.1:5173',
    'http://localhost:5173',
    'http://127.0.0.1:8080',
    'http://localhost:8080',
]
cors_origins = list(dict.fromkeys(env_cors_origins + configured_cors_origins))

app = FastAPI(title='GPP Local AI Engine', version='1.2.0', lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)

app.mount('/assets', StaticFiles(directory=MODELS_DIR), name='assets')

app.include_router(bootstrap_router)
app.include_router(models_router)
app.include_router(chat_router)
app.include_router(settings_router)
app.include_router(compat_router)
app.include_router(system_router)


# Desktop integration routes are owned by tools/exe_launcher.py only.
# The plain backend deliberately has no /api/desktop/* routes, so the EXE
# cannot accidentally read the old LocalAppData shared_chat_v23 store.


frontend_dist = PROJECT_ROOT / 'frontend' / 'dist'
if frontend_dist.exists():
    app.mount('/', StaticFiles(directory=frontend_dist, html=True), name='frontend')