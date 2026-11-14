"""Runtime diagnostic routes. Desktop hosting is an optional callback provider."""
import os
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from backend.app import diagnostic_chat as chat, desktop_host, core
from backend.app.version import APP_VERSION
from backend.app.ui_resources import FRONTEND_DIST

router=APIRouter(tags=['diagnostic chat'])


def response(data):
    return JSONResponse(data,headers=headers())


def headers():
    return {'X-Local-AI-GPP-Sync':chat.PROTOCOL_MARKER,'X-Local-AI-GPP-Desktop':'1' if desktop_host.active() else '0',
            'Cache-Control':'no-store, no-cache, must-revalidate, max-age=0','Pragma':'no-cache','Expires':'0'}


@router.get('/api/desktop/chat-sync')
def get_chat():return response(chat.read_chat_state())


@router.post('/api/desktop/chat-sync')
@router.put('/api/desktop/chat-sync')
def sync_chat(payload:dict):return response(chat.write_chat_state(payload))


@router.delete('/api/desktop/chat-sync')
@router.post('/api/desktop/chat-reset')
def reset_chat():return response(chat.reset_chat_state())


@router.post('/api/desktop/chat-clear')
def clear_chat(payload:dict | None=None):return response(chat.clear_chat_conversation((payload or {}).get('conversationId')))


@router.post('/api/desktop/chat-message')
def chat_message(payload:dict):return response(chat.upsert_shared_chat_message(payload))


@router.post('/api/desktop/chat-send')
def send(payload:dict):
    result=chat.submit_shared_chat_message(payload)
    if isinstance(result,JSONResponse):result.headers.update(headers());return result
    return response(result)


@router.get('/api/desktop/diagnostics')
def diagnostics(request:Request):
    if desktop_host.active():return response(desktop_host.invoke('diagnostics'))
    worker=core.external_worker_python()
    return response({'ok':True,'marker':chat.PROTOCOL_MARKER,'version':APP_VERSION,'pid':os.getpid(),'port':chat.server_port,
                     'runtime_dir':str(core.PROJECT_ROOT),'frontend_dist':str(FRONTEND_DIST),'frontend_dist_exists':FRONTEND_DIST.exists(),
                     'worker_python':worker,'models_dir':str(core.MODELS_DIR),'desktop':False,
                     'routes':[{'path':r.path,'methods':sorted(r.methods or [])} for r in request.app.routes if getattr(r,'path','').startswith('/api/desktop')]})


@router.post('/api/desktop/show-main')
def show_main():return desktop_host.invoke('show_main')


@router.post('/api/desktop/hide-main')
def hide_main():return desktop_host.invoke('hide_main')


@router.post('/api/desktop/show-assistant')
def show_assistant():return desktop_host.invoke('show_assistant')


@router.post('/api/desktop/hide-assistant')
def hide_assistant():return desktop_host.invoke('hide_assistant')


@router.post('/api/desktop/toggle-assistant')
def toggle_assistant():return desktop_host.invoke('toggle_assistant')


@router.post('/api/desktop/exit')
def exit_desktop():return desktop_host.invoke('exit')
