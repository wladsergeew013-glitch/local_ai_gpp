from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
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
from backend.app.routers.runtime_api import router as runtime_api_router
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
async def _runtime_lifespan(app: FastAPI):
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



@asynccontextmanager
async def lifespan(app: FastAPI):
    from backend.app.diagnostic_chat import assistant_state_dir, recover_interrupted_chat
    from backend.app.instance_lock import owned_instance
    with owned_instance(assistant_state_dir()/'.instance.lock'):
        recover_interrupted_chat()
        async with _runtime_lifespan(app):
            yield


settings = load_settings()
server_settings = settings.get('server', {})
env_cors_origins = _normalize_origins(os.getenv('LOCAL_AI_GPP_CORS_ORIGINS', ''))
configured_cors_origins = _normalize_origins(server_settings.get('cors_origins'))

cors_origins = list(dict.fromkeys(env_cors_origins + configured_cors_origins))

app = FastAPI(title='GPP Local AI Engine', version=APP_VERSION, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
    expose_headers=['X-Local-AI-GPP-Sync', 'X-Local-AI-GPP-Desktop', 'X-Local-AI-Request-ID', 'X-Local-AI-Cancel-Token', 'X-Local-AI-API-Version', 'ETag'],
)
app.add_middleware(LocalBrowserBoundaryMiddleware, allowed_origins=cors_origins)

app.mount('/assets/branding', StaticFiles(directory=MODELS_DIR / 'branding'), name='branding')

app.include_router(bootstrap_router)
app.include_router(models_router)
app.include_router(chat_router)
app.include_router(settings_router)
app.include_router(compat_router)
app.include_router(system_router)
app.include_router(runtime_api_router)


@app.exception_handler(__import__('fastapi').HTTPException)
async def runtime_http_error(request: Request, exc):
    from fastapi.responses import JSONResponse
    if not request.url.path.startswith('/v1/'):
        return JSONResponse({'detail': exc.detail}, status_code=exc.status_code, headers=exc.headers)
    detail = exc.detail
    message = detail.get('message', str(detail)) if isinstance(detail, dict) else str(detail)
    known = ('SETTINGS_POLICY_UNAVAILABLE', 'TOOL_CALL_INCOMPLETE', 'TOOL_CALL_INVALID', 'TOOL_CALL_REQUIRED',
             'TOOL_CHOICE_MISMATCH', 'TOOLS_UNSUPPORTED')
    code = detail.get('code') if isinstance(detail, dict) else next((c for c in known if message.startswith(c)), None)
    if not code:
        code = {401:'AUTH_REQUIRED',403:'FORBIDDEN',404:'MODEL_NOT_FOUND',409:'RUNTIME_BUSY',499:'REQUEST_CANCELLED',
                502:'WORKER_FAILED',503:'RUNTIME_UNAVAILABLE',504:'WORKER_TIMEOUT'}.get(exc.status_code, 'INVALID_REQUEST' if exc.status_code < 500 else 'INFERENCE_FAILED')
        if 'контекст' in message.lower() or 'context' in message.lower():
            code = 'CONTEXT_OVERFLOW'
    return JSONResponse({'detail': detail, 'error': {'code': code, 'message': message}}, status_code=exc.status_code, headers=exc.headers)


# The ASGI service owns its routes, storage and static UI; no desktop imports.
from backend.app.routers.diagnostic import router as diagnostic_router, headers as diagnostic_headers
from backend.app import diagnostic_chat, ui_resources
app.include_router(diagnostic_router)


@app.middleware('http')
async def diagnostic_marker(request: Request, call_next):
    server = request.scope.get('server')
    if server and isinstance(server[1],int):diagnostic_chat.server_port=server[1]
    result=await call_next(request)
    if request.scope.get('path','').startswith('/api/desktop'):result.headers.update(diagnostic_headers())
    return result


ui_resources.install(app)
