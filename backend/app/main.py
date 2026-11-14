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
    unload_all_runtimes,
)
from backend.app.routers.bootstrap import router as bootstrap_router
from backend.app.routers.chat import router as chat_router
from backend.app.routers.compat import router as compat_router
from backend.app.routers.models import router as models_router
from backend.app.routers.settings import router as settings_router
from backend.app.routers.system import router as system_router
from backend.app.version import APP_VERSION
from backend.app.browser_boundary import LocalBrowserBoundaryMiddleware


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
    async def preload_one() -> None:
        if runtime_settings.get('preload_on_start'):
            try:
                candidates = [m for m in load_models() if m.get('type') == 'LLM' and m.get('file_exists')]
                model = next((m for m in candidates if m.get('status') in {'running', 'warm'}), candidates[0] if candidates else None)
                if model:
                    await asyncio.to_thread(prewarm_runtime, str(model['id']))
            except Exception:
                pass
    preload = asyncio.create_task(preload_one())

    stop_event = asyncio.Event()

    async def runtime_maintenance_loop() -> None:
        while not stop_event.is_set():
            try:
                await asyncio.to_thread(enforce_idle_runtime_policy)
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
        if not preload.done():
            from backend.app.routers.system import cancel_generation
            await asyncio.to_thread(cancel_generation)
            preload.cancel()
        await asyncio.gather(preload, return_exceptions=True)
        await task
        from backend.app.routers.system import cancel_generation
        await asyncio.to_thread(cancel_generation)
        for _ in range(100):
            try:
                await asyncio.to_thread(unload_all_runtimes)
                break
            except Exception:
                await asyncio.sleep(.05)


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

app = FastAPI(title='GPP Local AI Engine', version=APP_VERSION, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
    expose_headers=['X-Local-AI-GPP-Sync', 'X-Local-AI-GPP-Desktop'],
)
app.add_middleware(LocalBrowserBoundaryMiddleware, allowed_origins=cors_origins)

app.mount('/assets/branding', StaticFiles(directory=MODELS_DIR / 'branding'), name='branding')

app.include_router(bootstrap_router)
app.include_router(models_router)
app.include_router(chat_router)
app.include_router(settings_router)
app.include_router(compat_router)
app.include_router(system_router)


# Browser, Docker and EXE use the same versioned store and generation owner.
# The launcher module is import-safe: Tk/WebView start only in its main().
import importlib
importlib.import_module('tools.exe_launcher')
