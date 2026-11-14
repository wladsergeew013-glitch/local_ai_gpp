from __future__ import annotations

import codecs
import contextlib
import copy
import json
import os
import queue
import random
import socket
import subprocess
import sys
import threading
import time
import traceback
import uuid
import urllib.error
import urllib.request
import urllib.parse
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

import uvicorn
from backend.app.context_budget import estimate_context, validate_context
from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse

APP_NAME = "Local AI Runtime"
PROXY_BYPASS_VALUE = "localhost,127.0.0.1,::1,[::1],*.localhost"
WEBVIEW2_PROXY_ARGS = "--no-proxy-server --proxy-bypass-list=<-loopback>;localhost;127.0.0.1;::1;[::1]"
TRANSPARENT_COLOR = "#010203"
LAUNCHER_VERSION_MARKER = "V67_4_DESKTOP_SYNC_MULTI_CONVERSATION"

server: uvicorn.Server | None = None
server_thread: threading.Thread | None = None
server_port = 0
main_window: Any | None = None
assistant_agent: "NativeAssistantAgent | None" = None
tray_icon: Any | None = None
webview_module: Any | None = None
quitting = False
desktop_ui_enabled = False


def monitor_work_area(x: int, y: int) -> tuple[int, int, int, int] | None:
    if os.name != 'nt':
        return None
    import ctypes
    from ctypes import wintypes

    class MonitorInfo(ctypes.Structure):
        _fields_ = [('cbSize', wintypes.DWORD), ('rcMonitor', wintypes.RECT),
                    ('rcWork', wintypes.RECT), ('dwFlags', wintypes.DWORD)]

    user32 = ctypes.windll.user32
    user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
    user32.MonitorFromPoint.restype = ctypes.c_void_p
    user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(MonitorInfo)]
    monitor = user32.MonitorFromPoint(wintypes.POINT(x, y), 2)
    info = MonitorInfo(cbSize=ctypes.sizeof(MonitorInfo))
    if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        work = info.rcWork
        return work.left, work.top, work.right, work.bottom
    return None


def runtime_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def resource_dir(name: str) -> Path:
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / name  # type: ignore[attr-defined]
    return runtime_dir() / name


def branding_asset(filename: str) -> Path:
    """Use bundled branding even when portable data is moved or replaced."""
    bundled = resource_dir("branding_icons") / filename
    if bundled.is_file():
        return bundled
    return runtime_dir() / "models_storage" / "branding" / "icons" / filename


def configure_windows_app_identity() -> None:
    if os.name == "nt":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("LocalAI.GPP.Desktop")


def app_state_dir() -> Path:
    base = os.getenv("LOCALAPPDATA") or os.getenv("APPDATA")
    path = Path(base) / "LocalAIGPP" if base else runtime_dir() / "tools" / "out" / "app_state"
    path.mkdir(parents=True, exist_ok=True)
    return path


def assistant_state_dir() -> Path:
    from backend.app.diagnostic_chat import assistant_state_dir as state_dir
    return state_dir()


def instance_file() -> Path:
    return app_state_dir() / "instance.json"


def chat_sync_file() -> Path:
    return assistant_state_dir() / "shared_chat_v67.json"


def assistant_position_file() -> Path:
    return assistant_state_dir() / "assistant_position_v58.json"


def assistant_settings_file() -> Path:
    return assistant_state_dir() / "assistant_settings_v58.json"


def merge_env_list(existing: str, required: str) -> str:
    items: list[str] = []
    for raw in (existing or "").replace(";", ",").split(","):
        item = raw.strip()
        if item and item not in items:
            items.append(item)
    for raw in required.replace(";", ",").split(","):
        item = raw.strip()
        if item and item not in items:
            items.append(item)
    return ",".join(items)


def configure_local_runtime_environment() -> None:
    no_proxy = merge_env_list(os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or "", PROXY_BYPASS_VALUE)
    os.environ["NO_PROXY"] = no_proxy
    os.environ["no_proxy"] = no_proxy
    os.environ["LOCAL_AI_GPP_PROXY_BYPASS"] = "1"

    existing_args = os.environ.get("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "").strip()
    if "no-proxy-server" not in existing_args and "proxy-bypass-list" not in existing_args:
        os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = (existing_args + " " + WEBVIEW2_PROXY_ARGS).strip()

    if getattr(sys, "frozen", False):
        dist = runtime_dir()
        worker = dist / "worker_runtime" / "python.exe"
        if worker.exists():
            os.environ["LOCAL_AI_GPP_WORKER_PYTHON"] = str(worker)
        os.environ["PYTHONPATH"] = str(dist) + os.pathsep + os.environ.get("PYTHONPATH", "")
        os.environ["LOCAL_AI_GPP_DIST_ROOT"] = str(dist)


configure_local_runtime_environment()

from backend.app.main import app  # noqa: E402
from backend.app.version import APP_VERSION  # noqa: E402
from backend.app.core import MODELS_DIR, load_settings  # noqa: E402

from backend.app import diagnostic_chat as chat_service
from backend.app.diagnostic_chat import (
    make_conversation_id,
    _new_conversation,
    _default_chat_state,
    ChatStateFormatError,
    _chat_file_signature,
    _read_cached_chat_state,
    message_completion_error,
    message_created_at,
    format_message_time,
    _normalize_chat_message,
    _normalize_chat_conversation,
    _message_count,
    _normalize_chat_state,
    _write_chat_state_raw,
    notify_main_chat_sync,
    read_chat_state,
    recover_interrupted_chat,
    _conversation_message_key,
    _message_sync_stamp,
    _upsert_message_list,
    _merge_message_lists,
    _find_conversation,
    _merge_incoming_chat_state_unlocked,
    upsert_shared_chat_message,
    write_chat_state,
    read_chat_state_unlocked,
    reset_chat_state,
    clear_chat_conversation,
    append_shared_message,
    _resolve_desktop_model_path,
    _desktop_model_is_available,
    _read_json_file,
    _select_desktop_chat_config,
    _shared_chat_generation_worker,
    submit_shared_chat_message,
    generation_lock,
    chat_sync_lock,
    _chat_generation_context,
    SHARED_CHAT_CONVERSATION_ID,
    SHARED_CHAT_TITLE,
)
from backend.app.ui_resources import FRONTEND_DIST

LOGS_DIR = runtime_dir() / "logs"
SHARED_CHAT_CONVERSATION_ID = "conv-shared"
SHARED_CHAT_TITLE = "Новый диалог"


def desktop_response_headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    headers = {
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
        "Expires": "0",
        "X-Local-AI-GPP-Sync": LAUNCHER_VERSION_MARKER,
        "X-Local-AI-GPP-Desktop": '1' if desktop_ui_enabled else '0',
    }
    if extra:
        headers.update(extra)
    return headers


def desktop_json_response(data: Any, *, status_code: int = 200) -> JSONResponse:
    return JSONResponse(data, status_code=status_code, headers=desktop_response_headers())


def ensure_desktop_marker(response: Any) -> Any:
    try:
        response.headers["X-Local-AI-GPP-Sync"] = LAUNCHER_VERSION_MARKER
        response.headers['X-Local-AI-GPP-Desktop'] = '1' if desktop_ui_enabled else '0'
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    except Exception:
        pass
    return response


def show_error(title: str, message: str) -> None:
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(title, message)
        root.destroy()
    except Exception:
        print(f"{title}: {message}", file=sys.stderr)


def show_info(title: str, message: str) -> None:
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo(title, message)
        root.destroy()
    except Exception:
        print(f"{title}: {message}")



def find_free_port() -> int:
    port = int(os.getenv('LOCAL_AI_GPP_PORT', str(load_settings()['server']['port'])))
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", port))
        return int(sock.getsockname()[1])


def wait_for_server(port: int, timeout: float = 25.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def write_instance_info(port: int) -> None:
    payload = {
        "pid": os.getpid(),
        "port": int(port),
        "url": f"http://127.0.0.1:{int(port)}",
        "api_base_url": f"http://127.0.0.1:{int(port)}/v1",
        "desktop_sync_marker": LAUNCHER_VERSION_MARKER,
        "version": APP_VERSION,
        "runtime_dir": str(runtime_dir()),
        "frontend_dist": str(FRONTEND_DIST),
        "written_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    with contextlib.suppress(Exception):
        instance_file().write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def open_folder(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def window_call(window: Any, method: str) -> None:
    if window is None:
        return
    try:
        fn = getattr(window, method, None)
        if callable(fn):
            fn()
    except Exception:
        pass


def show_main_window() -> dict[str, Any]:
    window_call(main_window, "show")
    window_call(main_window, "restore")
    window_call(main_window, "bring_to_front")
    return {"ok": True}


def hide_main_window() -> dict[str, Any]:
    window_call(main_window, "hide")
    return {"ok": True}


def show_assistant_window() -> dict[str, Any]:
    if assistant_agent is not None:
        assistant_agent.show()
    return {"ok": True}


def hide_assistant_window() -> dict[str, Any]:
    if assistant_agent is not None:
        assistant_agent.hide()
    return {"ok": True}


def toggle_assistant_window() -> dict[str, Any]:
    if assistant_agent is not None:
        assistant_agent.toggle_visible()
    return {"ok": True}


def request_exit() -> None:
    global quitting
    quitting = True
    from backend.app.core import RUNTIMES
    for entry in list(RUNTIMES.values()):
        with contextlib.suppress(Exception):
            entry['runtime'].close()
    try:
        if tray_icon is not None:
            tray_icon.stop()
    except Exception:
        pass
    try:
        if assistant_agent is not None:
            assistant_agent.destroy()
    except Exception:
        pass
    try:
        if server is not None:
            server.should_exit = True
    except Exception:
        pass
    with contextlib.suppress(Exception):
        instance_file().unlink(missing_ok=True)
    try:
        window_call(main_window, "destroy")
    except Exception:
        pass
    if getattr(sys, "frozen", False):
        os._exit(0)


def unload_models_from_tray() -> None:
    try:
        port = int(server_port or 0)
        if not port:
            return
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/runtime/status", timeout=5) as response:
            rows = json.loads(response.read().decode("utf-8", "replace") or "[]")
        for row in rows if isinstance(rows, list) else []:
            model_id = str(row.get("model_id") or "")
            if not model_id:
                continue
            req = urllib.request.Request(f"http://127.0.0.1:{port}/api/models/{urllib.parse.quote(model_id)}/unload", method="POST")
            with urllib.request.urlopen(req, timeout=10) as response:
                response.read()
        if assistant_agent is not None:
            assistant_agent.set_state("ready", "Помощник", "Модели выгружены.")
    except Exception as exc:
        if assistant_agent is not None:
            assistant_agent.set_state("error", "Ошибка", str(exc)[:90])


def desktop_diagnostics() -> dict[str, Any]:
    routes = []
    for route in getattr(app.router, "routes", []):
        route_path = str(getattr(route, "path", "") or "")
        if route_path.startswith("/api/desktop"):
            routes.append({
                "path": route_path,
                "methods": sorted(list(getattr(route, "methods", []) or [])),
                "name": str(getattr(route, "name", "") or ""),
            })
    worker = runtime_dir() / "worker_runtime" / "python.exe"
    return {
        "ok": True,
        "marker": LAUNCHER_VERSION_MARKER,
        "version": APP_VERSION,
        "pid": os.getpid(),
        "port": int(server_port or 0),
        "runtime_dir": str(runtime_dir()),
        "frontend_dist": str(FRONTEND_DIST),
        "frontend_dist_exists": FRONTEND_DIST.exists(),
        "worker_python": str(worker),
        "worker_python_exists": worker.exists(),
        "models_dir": str(MODELS_DIR),
        "models_json_exists": (MODELS_DIR / "models.json").exists(),
        "app_icon": str(branding_asset("mini_agent_head_v2.ico")),
        "app_icon_exists": branding_asset("mini_agent_head_v2.ico").is_file(),
        "tray_icon": str(branding_asset("mini_agent_head_v2.png")),
        "tray_icon_exists": branding_asset("mini_agent_head_v2.png").is_file(),
        "assistant_dialog_menu_ready": assistant_agent is not None and assistant_agent.dialog_listbox is not None,
        "assistant_dialog_ids": list(assistant_agent.dialog_ids) if assistant_agent is not None else [],
        "routes": routes,
    }


def clamp_int(value: Any, min_value: int, max_value: int, default: int) -> int:
    try:
        number = int(value)
    except Exception:
        number = default
    return max(min_value, min(max_value, number))


def _default_assistant_settings() -> dict[str, Any]:
    # V54 defaults are intentionally closer to the earlier compact helper: smaller avatar,
    # tighter bubble and less "desktop form" feeling.
    return {
        "avatar_width": 154,
        "avatar_height": 210,
        "chat_width": 512,
        "chat_height": 304,
        "font_size": 10,
        "always_on_top": True,
    }


def read_assistant_settings() -> dict[str, Any]:
    base = _default_assistant_settings()
    path = assistant_settings_file()
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                base.update(raw)
        except Exception:
            pass
    base["avatar_width"] = clamp_int(base.get("avatar_width"), 120, 260, 154)
    base["avatar_height"] = clamp_int(base.get("avatar_height"), 160, 340, 210)
    base["chat_width"] = clamp_int(base.get("chat_width"), 420, 760, 512)
    base["chat_height"] = clamp_int(base.get("chat_height"), 260, 900, 304)
    base["font_size"] = clamp_int(base.get("font_size"), 9, 14, 10)
    base["always_on_top"] = bool(base.get("always_on_top", True))
    return base


def write_assistant_settings(settings: dict[str, Any]) -> dict[str, Any]:
    normalized = read_assistant_settings()
    normalized.update(settings if isinstance(settings, dict) else {})
    normalized["avatar_width"] = clamp_int(normalized.get("avatar_width"), 120, 260, 154)
    normalized["avatar_height"] = clamp_int(normalized.get("avatar_height"), 160, 340, 210)
    normalized["chat_width"] = clamp_int(normalized.get("chat_width"), 420, 760, 512)
    normalized["chat_height"] = clamp_int(normalized.get("chat_height"), 260, 900, 304)
    normalized["font_size"] = clamp_int(normalized.get("font_size"), 9, 14, 10)
    normalized["always_on_top"] = bool(normalized.get("always_on_top", True))
    assistant_settings_file().write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
    return normalized


class NativeAssistantAgent:
    def __init__(self, get_port: Any) -> None:
        self.get_port = get_port
        self.root: Any | None = None
        self.ui_events: queue.SimpleQueue = queue.SimpleQueue()
        self.chat: Any | None = None
        self.tail: Any | None = None
        self.tail_canvas: Any | None = None
        self.tk: Any | None = None
        self.Image = None
        self.ImageTk = None
        self.frames: dict[str, list[Any]] = {"ready": [], "thinking": [], "speaking": [], "error": []}
        self.frame_index = 0
        self.state = "ready"
        self.badge_text = "✓"
        self.status_title = "На связи"
        self.status_text = "Готов помочь"
        self.bubble_open = False
        self.drag_start: tuple[int, int, int, int] | None = None
        self.drag_moved = False
        self.drag_origin_widget: Any | None = None
        self.drag_poll_after: str | None = None
        self.sync_poll_after: str | None = None
        self.last_chat_sync_updated_at = 0.0
        self.last_rendered_conversation_id = ""
        self.active_dialog_title = ""
        self.dialog_sidebar: Any | None = None
        self.dialog_listbox: Any | None = None
        self.chat_body: Any | None = None
        self.dialog_ids: list[str] = []
        self.dialog_list_signature: Any = None
        self.dialog_sidebar_open = False
        self.busy_request = False
        self.model_id = ""
        self.temperature = 0.2
        self.max_tokens = 1024
        self.runtime: dict[str, Any] = {}
        self.runtime_settings_stamp = 0
        self.settings = read_assistant_settings()
        self.avatar_w = int(self.settings["avatar_width"])
        self.avatar_h = int(self.settings["avatar_height"])
        self.chat_w = int(self.settings["chat_width"])
        self.chat_h = int(self.settings["chat_height"])
        self.font_size = int(self.settings["font_size"])
        self.always_on_top = bool(self.settings["always_on_top"])
        self.avatar_x = 980
        self.avatar_y = 350
        self.avatar_label: Any | None = None
        self.badge_label: Any | None = None
        self.status_label: Any | None = None
        self.title_label: Any | None = None
        self.history_box: Any | None = None
        self.entry_widget: Any | None = None
        self.send_button: Any | None = None
        self.close_button: Any | None = None
        self.resize_edges: dict[str, Any] = {}
        self.resize_start: dict[str, Any] | None = None
        self.resize_poll_after: str | None = None
        self.chat_side = "left"
        self.tail_side: str | None = None
        self.compose_frame: Any | None = None
        self.snake_frame: Any | None = None
        self.snake_board: Any | None = None
        self.snake_score_label: Any | None = None
        self.snake_after: str | None = None
        self.snake_active = False
        self.snake_over = False
        self.snake_body: list[tuple[int, int]] = []
        self.snake_food = (0, 0)
        self.snake_direction = (1, 0)
        self.snake_next_direction = (1, 0)
        self.snake_score = 0
        self.awaiting_answer = False
        self.pending_answer_id = ""
        self.reveal_message_id = ""
        self.last_revealed_answer_id = ""
        self.reveal_text = ""
        self.reveal_index = 0
        self.reveal_after: str | None = None
        self.input_var: Any | None = None
        self.settings_window: Any | None = None
        self.context_window: Any | None = None
        self.context_label: Any | None = None
        self._visible = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        import tkinter as tk
        from tkinter import Menu, Text, ttk

        self.tk = tk
        try:
            from PIL import Image, ImageTk
            self.Image = Image
            self.ImageTk = ImageTk
        except Exception:
            self.Image = None
            self.ImageTk = None

        self._load_position()
        self.root = tk.Tk()
        self.root.title(f"Local AI GPP Помощник v{APP_VERSION}")
        app_icon = branding_asset("mini_agent_head_v2.ico")
        if os.name == "nt" and app_icon.is_file():
            self.root.iconbitmap(default=str(app_icon))
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", self.always_on_top)
        self.root.configure(bg=TRANSPARENT_COLOR)
        self.root.wm_attributes("-transparentcolor", TRANSPARENT_COLOR)
        self.root.geometry(f"{self.avatar_w}x{self.avatar_h}+{self.avatar_x}+{self.avatar_y}")

        self.avatar_label = tk.Label(self.root, bg=TRANSPARENT_COLOR, borderwidth=0, highlightthickness=0)
        self.avatar_label.place(x=0, y=0, width=self.avatar_w, height=self.avatar_h)
        self.badge_label = tk.Label(self.root, text="✓", bg="#25a957", fg="white", font=("Segoe UI", 17, "bold"), borderwidth=2, relief="solid")
        self._place_badge()

        # Bind LMB only to real mascot widgets. Binding the root as well makes Tk
        # run the same handler twice through widget/toplevel bindtags, which opens
        # and immediately closes the chat on a single click.
        for widget in (self.avatar_label, self.badge_label):
            widget.bind("<ButtonPress-1>", self._begin_drag)
            widget.bind("<Button-3>", self._show_avatar_menu)
            if os.name != "nt":
                widget.bind("<B1-Motion>", self._move_drag)
                widget.bind("<ButtonRelease-1>", self._end_drag)
        self.root.bind("<Button-3>", self._show_avatar_menu)

        self.avatar_menu = Menu(self.root, tearoff=0)
        self.avatar_menu.add_command(label="Открыть чат", command=self._menu_command(self.show_chat))
        self.avatar_menu.add_command(label="Открыть полный интерфейс", command=self._menu_command(show_main_window))
        self.avatar_menu.add_separator()
        self.avatar_menu.add_command(label="Поверх всех окон", command=self._menu_command(self._toggle_topmost))
        self.avatar_menu.add_command(label="Настройки помощника...", command=self._menu_command(self.show_settings))
        self.avatar_menu.add_command(label="Контекст и длина ответа...", command=self._menu_command(self._show_context_dialog))
        self.avatar_menu.add_command(label="Обновить состояние модели", command=self._menu_command(lambda: threading.Thread(target=self._refresh_bootstrap, daemon=True).start()))
        self.avatar_menu.add_command(label="Выгрузить модели", command=self._menu_command(lambda: threading.Thread(target=unload_models_from_tray, daemon=True).start()))
        self.avatar_menu.add_separator()
        self.avatar_menu.add_command(label="Открыть models_storage", command=self._menu_command(lambda: open_folder(MODELS_DIR)))
        self.avatar_menu.add_command(label="Открыть логи", command=self._menu_command(lambda: open_folder(LOGS_DIR)))
        self.avatar_menu.add_command(label="Скрыть помощника", command=self._menu_command(self.hide))
        self.avatar_menu.add_separator()
        self.avatar_menu.add_command(label="Выход", command=self._menu_command(request_exit))

        self._create_chat_window(tk, Text, ttk)
        self._render_shared_history(force=True)
        self._schedule_chat_sync_poll()
        self._load_frames()
        # HTTP can wait for a cold model/server. Keep Tk's event loop free.
        threading.Thread(target=self._refresh_bootstrap, daemon=True).start()
        self._animate()
        self.root.mainloop()

    def _create_chat_window(self, tk: Any, Text: Any, ttk: Any) -> None:
        self.chat = tk.Toplevel(self.root)
        self.chat.title(f"Local AI GPP Chat v{APP_VERSION}")
        self.chat.overrideredirect(True)
        self.chat.attributes("-topmost", self.always_on_top)
        self.chat.configure(bg="#0e4f97")
        self.chat.geometry(f"{self.chat_w}x{self.chat_h}+{max(10, self.avatar_x - self.chat_w - 20)}+{max(10, self.avatar_y + 10)}")
        self.chat.withdraw()

        self.tail = tk.Toplevel(self.root)
        self.tail.overrideredirect(True)
        self.tail.attributes("-topmost", self.always_on_top)
        self.tail.configure(bg=TRANSPARENT_COLOR)
        self.tail.wm_attributes("-transparentcolor", TRANSPARENT_COLOR)
        self.tail.geometry("34x46+0+0")
        self.tail_canvas = tk.Canvas(self.tail, width=34, height=46, bg=TRANSPARENT_COLOR, highlightthickness=0, borderwidth=0)
        self.tail_canvas.pack(fill="both", expand=True)
        # Tail is a visual pointer only. It must not be draggable or clickable.
        for tail_widget in (self.tail, self.tail_canvas):
            tail_widget.bind("<ButtonPress-1>", lambda _e: "break")
            tail_widget.bind("<B1-Motion>", lambda _e: "break")
            tail_widget.bind("<ButtonRelease-1>", lambda _e: "break")
            tail_widget.bind("<Button-3>", self._show_chat_menu)
        self.tail.withdraw()

        header = tk.Frame(self.chat, bg="#1d559c", height=56)
        header.pack(fill="x")
        header.pack_propagate(False)
        self.title_label = tk.Label(header, text="На связи", bg="#1d559c", fg="white", font=("Segoe UI", 18, "bold"), anchor="w")
        self.title_label.place(x=54, y=8, width=300, height=26)
        self.status_label = tk.Label(header, text=f"●  Готов помочь · v{APP_VERSION}", bg="#1d559c", fg="#53f079", font=("Segoe UI", 9, "bold"), anchor="w")
        self.status_label.place(x=54, y=34, width=300, height=16)
        self.dialog_toggle_button = tk.Label(header, text="☰", bg="#2f67ad", fg="white", font=("Segoe UI", 17, "bold"), cursor="hand2")
        self.dialog_toggle_button.place(x=12, y=11, width=32, height=34)
        self.dialog_toggle_button.bind("<Button-1>", lambda _e: self._toggle_dialog_sidebar())
        self.close_button = tk.Label(header, text="×", bg="#2f67ad", fg="white", font=("Segoe UI", 18, "bold"), cursor="hand2")
        self.close_button.place(x=self.chat_w - 46, y=11, width=34, height=34)
        self.close_button.bind("<Button-1>", lambda _e: self.hide_chat())

        content = tk.Frame(self.chat, bg="#fbfdff")
        content.pack(fill="both", expand=True, padx=4, pady=(0, 4))
        self.dialog_sidebar = tk.Frame(content, bg="#edf4fb", width=166, padx=6, pady=10)
        self.dialog_sidebar.pack_propagate(False)
        tk.Button(self.dialog_sidebar, text="＋ Новый диалог", command=self._new_assistant_dialog,
                  bg="#ffffff", fg="#155eb9", relief="flat", font=("Segoe UI", 9)).pack(fill="x", pady=(0, 8))
        tk.Button(self.dialog_sidebar, text="Скрыть меню", command=self._toggle_dialog_sidebar,
                  bg="#edf4fb", fg="#155eb9", relief="flat", font=("Segoe UI", 9)).pack(side="bottom", fill="x", pady=(6, 0))
        dialog_list_frame = tk.Frame(self.dialog_sidebar, bg="#edf4fb")
        dialog_list_frame.pack(fill="both", expand=True)
        self.dialog_listbox = tk.Listbox(dialog_list_frame, bg="#edf4fb", fg="#16324e", selectbackground="#c7ddf8",
                                        selectforeground="#145aa0", relief="flat", bd=0, highlightthickness=0,
                                        exportselection=False, activestyle="none", font=("Segoe UI", 9))
        dialog_scroll = tk.Scrollbar(dialog_list_frame, command=self.dialog_listbox.yview)
        dialog_scroll.pack(side="right", fill="y")
        self.dialog_listbox.configure(yscrollcommand=dialog_scroll.set)
        self.dialog_listbox.pack(side="left", fill="both", expand=True)
        self.dialog_listbox.bind("<<ListboxSelect>>", self._select_assistant_dialog)
        body = tk.Frame(content, bg="#fbfdff", padx=14, pady=12)
        body.pack(side="left", fill="both", expand=True)
        self.chat_body = body
        self.history_box = Text(body, wrap="word", bg="#fbfdff", fg="#0b1b33", relief="flat", borderwidth=0, highlightthickness=0, font=("Segoe UI", self.font_size), height=9)
        self.history_box.tag_configure("user", foreground="#155eb9", font=("Segoe UI", self.font_size, "bold"), justify="right", lmargin1=40, lmargin2=40, rmargin=4)
        self.history_box.tag_configure("assistant", foreground="#155eb9", font=("Segoe UI", self.font_size, "bold"), justify="left", lmargin1=4, lmargin2=4, rmargin=40)
        self.history_box.tag_configure('error', foreground='#ba3c32', justify='left', lmargin1=4, lmargin2=4, rmargin=40)
        for role in ("user", "assistant"):
            alignment = "right" if role == "user" else "left"
            self.history_box.tag_configure(f"body-{role}", justify=alignment, lmargin1=40 if role == "user" else 4, lmargin2=40 if role == "user" else 4, rmargin=4 if role == "user" else 40)
            self.history_box.tag_configure(f"timestamp-{role}", foreground="#66809e", font=("Segoe UI", max(9, self.font_size - 2)), justify=alignment)
        self.history_box.insert("end", "На связи. Напиши вопрос ниже — отвечу с уже выбранной локальной модели.\n")
        self.history_box.configure(state="disabled")
        self.history_box.bind("<Control-KeyPress>", self._history_control_key)
        self.history_box.bind("<Button-3>", self._show_history_menu)

        bottom = tk.Frame(body, bg="#fbfdff")
        bottom.pack(side="bottom", fill="x", pady=(12, 0))
        self.compose_frame = bottom
        self.input_var = tk.StringVar()
        self.input_var.trace_add("write", lambda *_args: (self._refresh_send_button(), self._refresh_context_hint()))
        self.entry_widget = tk.Entry(bottom, textvariable=self.input_var, font=("Segoe UI", max(11, self.font_size + 1)), relief="solid", bd=1, highlightthickness=2, highlightbackground="#b8d0ea", highlightcolor="#1d5fa8")
        self.entry_widget.pack(side="left", fill="x", expand=True, ipady=7)
        self.entry_widget.bind("<Return>", lambda _e: self._send_message())
        self.entry_widget.bind("<Control-KeyPress>", self._entry_control_key)
        self.entry_widget.bind("<Shift-Insert>", self._paste_from_clipboard)
        self.entry_widget.bind("<Button-3>", self._show_entry_menu)
        self.send_button = tk.Label(bottom, text="↵", bg="#b5cbe2", fg="white", font=("Segoe UI", 21, "bold"), cursor="arrow")
        self.send_button.pack(side="left", padx=(10, 0), ipadx=16, ipady=3)
        self.send_button.bind("<Button-1>", lambda _e: self._send_message())
        self._refresh_send_button()

        context_bar = tk.Frame(body, bg="#fbfdff")
        context_bar.pack(side="bottom", fill="x", pady=(6, 0))
        self.context_label = tk.Label(context_bar, text="", bg="#fbfdff", fg="#66809e", anchor="w", font=("Segoe UI", 9))
        self.context_label.pack(side="left", fill="x", expand=True)
        tk.Button(context_bar, text="Контекст", command=self._show_context_dialog, bg="#edf4fb", fg="#155eb9", relief="flat", font=("Segoe UI", 9)).pack(side="right")

        self._create_snake_panel(tk, body)
        self.history_box.pack(fill="both", expand=True)
        self.chat.bind("<KeyPress>", self._snake_key)
        self.entry_menu = tk.Menu(self.entry_widget, tearoff=0)
        self.entry_menu.add_command(label="Вставить", command=self._paste_from_clipboard)
        self.entry_menu.add_command(label="Копировать", command=lambda: self.entry_widget.event_generate("<<Copy>>"))
        self.entry_menu.add_command(label="Вырезать", command=lambda: self.entry_widget.event_generate("<<Cut>>"))
        self.entry_menu.add_separator()
        self.entry_menu.add_command(label="Выделить всё", command=lambda: self.entry_widget.select_range(0, "end"))
        self.history_menu = tk.Menu(self.history_box, tearoff=0)
        self.history_menu.add_command(label="Копировать выделенное", command=self._copy_history_selection)
        self.history_menu.add_command(label="Копировать весь текст", command=self._copy_all_history)
        for edge, cursor in (
            ("bottom", "sb_v_double_arrow"),
            ("right", "sb_h_double_arrow"),
            ("bottom-right", "bottom_right_corner"),
        ):
            strip = tk.Frame(self.chat, bg="#0e4f97", cursor=cursor)
            strip.bind("<ButtonPress-1>", lambda event, value=edge: self._begin_chat_resize(event, value))
            strip.bind("<B1-Motion>", self._move_chat_resize)
            strip.bind("<ButtonRelease-1>", self._end_chat_resize)
            self.resize_edges[edge] = strip
        self._place_resize_edges()

        for widget in (self.chat, header, body, self.title_label, self.status_label):
            widget.bind("<Button-3>", self._show_chat_menu)

        self.chat_menu = tk.Menu(self.chat, tearoff=0)
        self.chat_menu.add_command(label="Диалоги", command=self._menu_command(self._toggle_dialog_sidebar))
        self.chat_menu.add_command(label="Контекст и длина ответа...", command=self._menu_command(self._show_context_dialog))
        self.chat_menu.add_command(label="Открыть полный интерфейс", command=self._menu_command(show_main_window))
        self.chat_menu.add_command(label="Скрыть чат", command=self._menu_command(self.hide_chat))
        self.chat_menu.add_command(label="Скрыть помощника", command=self._menu_command(self.hide))
        self.chat_menu.add_separator()
        self.chat_menu.add_command(label="Очистить историю", command=self._menu_command(self._clear_history))
        self.chat_menu.add_command(label="Выгрузить модели", command=self._menu_command(lambda: threading.Thread(target=unload_models_from_tray, daemon=True).start()))
        self.chat_menu.add_separator()
        self.chat_menu.add_command(label="Выход", command=self._menu_command(request_exit))

    def _toggle_dialog_sidebar(self) -> None:
        if self.dialog_sidebar is None or self.chat_body is None:
            return
        self.dialog_sidebar_open = not self.dialog_sidebar_open
        if self.dialog_sidebar_open:
            self.dialog_sidebar.pack(side="left", fill="y", before=self.chat_body)
            self._refresh_dialog_list(read_chat_state())
        else:
            self.dialog_sidebar.pack_forget()

    def _refresh_dialog_list(self, state: dict[str, Any]) -> None:
        if self.dialog_listbox is None:
            return
        conversations = state.get("conversations") or []
        active_id = state.get("activeConversationId")
        signature = (active_id, tuple((c["id"], c.get("title"), len(c.get("messages") or [])) for c in conversations))
        if signature == self.dialog_list_signature:
            return
        self.dialog_list_signature = signature
        self.dialog_ids = [c["id"] for c in conversations]
        self.dialog_listbox.delete(0, "end")
        for index, conversation in enumerate(conversations):
            title = str(conversation.get("title") or "Новый диалог")
            label = title if len(title) <= 18 else title[:17] + "…"
            self.dialog_listbox.insert("end", f"{label} · {len(conversation.get('messages') or [])}")
            if conversation["id"] == active_id:
                self.dialog_listbox.selection_set(index)
                self.dialog_listbox.see(index)

    def _finish_dialog_switch(self) -> None:
        if self.reveal_after and self.root is not None:
            self.root.after_cancel(self.reveal_after)
        self.reveal_after = None
        self.reveal_message_id = ""
        self.reveal_text = ""
        self.awaiting_answer = False
        self.pending_answer_id = ""
        self._render_shared_history(force=True)

    def _select_assistant_dialog(self, _event: Any = None) -> None:
        selected = self.dialog_listbox.curselection() if self.dialog_listbox is not None else ()
        if not selected or selected[0] >= len(self.dialog_ids):
            return
        state = read_chat_state()
        target_id = self.dialog_ids[selected[0]]
        if target_id == state.get("activeConversationId"):
            return
        write_chat_state({**state, "activeConversationId": target_id, "source": "assistant-dialog-select"})
        self._finish_dialog_switch()

    def _new_assistant_dialog(self) -> None:
        state = read_chat_state()
        conversation = _new_conversation(title="Новый диалог")
        write_chat_state({**state, "activeConversationId": conversation["id"],
                          "conversations": [conversation, *state["conversations"]], "source": "new-conversation"})
        self._finish_dialog_switch()

    def _display_chat_height(self) -> int:
        height = max(self.chat_h, 430 if self.snake_active else 260)
        if self.root is not None:
            _, top, _, bottom = self._monitor_work_area()
            height = min(height, max(260, bottom - top - 24))
        return height

    def _monitor_work_area(self) -> tuple[int, int, int, int]:
        area = monitor_work_area(self.avatar_x + self.avatar_w // 2, self.avatar_y + self.avatar_h // 2)
        if area is not None:
            return area
        return (0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight()) if self.root is not None else (0, 0, 1600, 900)

    def _place_resize_edges(self) -> None:
        if not self.resize_edges:
            return
        width, height = self.chat_w, self._display_chat_height()
        positions = {
            "bottom": (0, height - 7, width - 12, 7),
            "right": (width - 7, 0, 7, height - 12),
            "bottom-right": (width - 12, height - 12, 12, 12),
        }
        for edge, (x, y, strip_width, strip_height) in positions.items():
            self.resize_edges[edge].place(x=x, y=y, width=strip_width, height=strip_height)

    def _begin_chat_resize(self, event: Any, edge: str) -> str:
        if self.chat is None:
            return "break"
        if self.resize_poll_after is not None and self.root is not None:
            self.root.after_cancel(self.resize_poll_after)
            self.resize_poll_after = None
        self.resize_start = {
            "edge": edge,
            "pointer_x": event.x_root,
            "pointer_y": event.y_root,
            "x": self.chat.winfo_rootx(),
            "y": self.chat.winfo_rooty(),
            "width": self.chat.winfo_width(),
            "height": self.chat.winfo_height(),
            "avatar_x": self.avatar_x,
            "side": self.chat_side,
            "work_area": self._monitor_work_area(),
        }
        if os.name == "nt" and self.root is not None:
            self.resize_poll_after = self.root.after(30, self._poll_chat_resize)
        return "break"

    def _move_chat_resize(self, event: Any) -> str:
        if os.name != "nt":
            self._update_chat_resize(event.x_root, event.y_root)
        return "break"

    def _poll_chat_resize(self) -> None:
        self.resize_poll_after = None
        if self.resize_start is None or self.root is None:
            return
        point = self._cursor_position()
        if point is not None:
            self._update_chat_resize(point[0], point[1])
        if not self._left_mouse_down():
            self._finish_chat_resize()
        else:
            self.resize_poll_after = self.root.after(30, self._poll_chat_resize)

    def _update_chat_resize(self, pointer_x: int, pointer_y: int) -> None:
        if self.resize_start is None or self.chat is None or self.root is None:
            return
        start = self.resize_start
        edge = str(start["edge"])
        side = str(start["side"])
        left, top, right, bottom = start["work_area"]
        base_x, base_y = int(start["x"]), int(start["y"])
        base_w, base_h = int(start["width"]), int(start["height"])
        dx = pointer_x - int(start["pointer_x"])
        dy = pointer_y - int(start["pointer_y"])
        width, height = base_w, base_h
        x = base_x
        if "right" in edge:
            if side == "left":
                max_width = min(760, right - left - 48 - self.avatar_w)
                width = max(420, min(max_width, base_w + dx))
                x = max(left + 12, min(base_x, right - width - self.avatar_w - 36))
                self.avatar_x = x + width + 24
                self.root.geometry(f"{self.avatar_w}x{self.avatar_h}+{self.avatar_x}+{self.avatar_y}")
            else:
                max_width = min(760, right - base_x - 12)
                width = max(420, min(max_width, base_w + dx))
        if "bottom" in edge:
            minimum = 430 if self.snake_active else 260
            max_height = min(900, bottom - base_y - 12)
            height = max(minimum, min(max_height, base_h + dy))
        if width == self.chat_w and height == self.chat_h and x == self.chat.winfo_rootx():
            return
        self.chat_w, self.chat_h = width, height
        self.chat.geometry(f"{width}x{height}+{x}+{base_y}")
        if self.close_button is not None:
            self.close_button.place(x=width - 46, y=11, width=34, height=34)
        self._place_resize_edges()
        self._place_tail(x, base_y, side)

    def _finish_chat_resize(self) -> None:
        if self.resize_start is None:
            return
        if self.resize_poll_after is not None and self.root is not None:
            self.root.after_cancel(self.resize_poll_after)
            self.resize_poll_after = None
        avatar_moved = self.avatar_x != int(self.resize_start["avatar_x"])
        self.resize_start = None
        if self.chat is not None and self.root is not None:
            self._place_tail(
                self.chat.winfo_rootx(), self.chat.winfo_rooty(), self.chat_side,
            )
            if self.tail is not None:
                self.tail.lift()
            self.root.lift()
        self.settings = write_assistant_settings({"chat_width": self.chat_w, "chat_height": self.chat_h})
        if avatar_moved:
            self._save_position()

    def _end_chat_resize(self, _event: Any) -> str:
        if self.resize_start is None:
            return "break"
        self._finish_chat_resize()
        return "break"

    def _paste_from_clipboard(self, _event: Any = None) -> str:
        if self.entry_widget is None:
            return "break"
        try:
            value = " ".join(self.entry_widget.clipboard_get().splitlines())
            if self.entry_widget.selection_present():
                self.entry_widget.delete("sel.first", "sel.last")
            self.entry_widget.insert("insert", value)
            self.entry_widget.focus_set()
        except Exception:
            pass
        return "break"

    def _entry_control_key(self, event: Any) -> str | None:
        key = str(event.keysym).lower()
        code = int(getattr(event, "keycode", 0) or 0)
        if code == 86 or key in {"v", "м"}:
            return self._paste_from_clipboard(event)
        if code == 65 or key in {"a", "ф"}:
            self.entry_widget.select_range(0, "end")
            self.entry_widget.icursor("end")
            return "break"
        return None

    def _history_control_key(self, event: Any) -> str | None:
        key = str(event.keysym).lower()
        code = int(getattr(event, "keycode", 0) or 0)
        if code == 65 or key in {"a", "ф"}:
            self.history_box.tag_add("sel", "1.0", "end-1c")
            return "break"
        if code == 67 or key in {"c", "с"}:
            return self._copy_history_selection()
        return None

    def _copy_history_selection(self, _event: Any = None) -> str:
        try:
            text = self.history_box.get("sel.first", "sel.last")
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.root.update_idletasks()
        except Exception:
            pass
        return "break"

    def _copy_all_history(self) -> None:
        if self.history_box is None or self.root is None:
            return
        text = self.history_box.get("1.0", "end-1c")
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update_idletasks()

    def _show_history_menu(self, event: Any) -> str:
        try:
            self.history_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.history_menu.grab_release()
        return "break"

    def _refresh_send_button(self) -> None:
        if self.send_button is None or self.input_var is None:
            return
        active = bool(self.input_var.get().strip())
        self.send_button.configure(bg="#2f6fed" if active else "#b5cbe2", cursor="hand2" if active else "arrow")

    def _show_entry_menu(self, event: Any) -> str:
        try:
            self.entry_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.entry_menu.grab_release()
        return "break"

    def _create_snake_panel(self, tk: Any, body: Any) -> None:
        panel = tk.Frame(body, bg="#edf6ff", highlightbackground="#a9c8e8", highlightthickness=2, padx=8, pady=6)
        self.snake_frame = panel
        heading = tk.Frame(panel, bg="#edf6ff")
        heading.pack(fill="x", pady=(0, 5))
        tk.Label(heading, text="Змейка · пока готовится ответ", bg="#edf6ff", fg="#174f90", font=("Segoe UI", 9, "bold")).pack(side="left")
        self.snake_score_label = tk.Label(heading, text="Счёт: 0", bg="#edf6ff", fg="#174f90", font=("Segoe UI", 9, "bold"))
        self.snake_score_label.pack(side="right")
        content = tk.Frame(panel, bg="#edf6ff")
        content.pack()
        self.snake_board = tk.Canvas(content, width=224, height=128, bg="#f8fbff", highlightbackground="#1d4f96", highlightthickness=2, takefocus=1)
        self.snake_board.pack(side="left", padx=(0, 18))
        controls = tk.Frame(content, bg="#edf6ff")
        controls.pack(side="left")
        for symbol, direction, row, column in (
            ("↑", (0, -1), 0, 1), ("←", (-1, 0), 1, 0),
            ("↓", (0, 1), 1, 1), ("→", (1, 0), 1, 2),
        ):
            tk.Button(controls, text=symbol, command=lambda value=direction: self._turn_snake(value),
                      width=2, bg="#d7e9fb", fg="#174f90", relief="solid", bd=1).grid(row=row, column=column, padx=1, pady=1)
        tk.Button(controls, text="Заново", command=self._reset_snake, bg="#d7e9fb", fg="#174f90", relief="solid", bd=1).grid(row=2, column=0, columnspan=3, sticky="ew", pady=(7, 0))

    def _new_snake_food(self) -> tuple[int, int]:
        free = [(x, y) for y in range(8) for x in range(14) if (x, y) not in self.snake_body]
        return random.choice(free) if free else self.snake_body[0]

    def _draw_snake(self) -> None:
        if self.snake_board is None:
            return
        board = self.snake_board
        board.delete("all")
        for y in range(8):
            for x in range(14):
                color = "#256bb8" if (x, y) in self.snake_body else "#ffffff"
                board.create_rectangle(x * 16 + 1, y * 16 + 1, x * 16 + 16, y * 16 + 16, fill=color, outline="#c6d9ed")
        if self.snake_food not in self.snake_body:
            x, y = self.snake_food
            board.create_oval(x * 16 + 3, y * 16 + 3, x * 16 + 14, y * 16 + 14, fill="#ef765e", outline="#be4e45")
        if self.snake_over:
            board.create_rectangle(28, 43, 196, 85, fill="#edf6ff", outline="#1d4f96")
            board.create_text(112, 64, text="Игра окончена", fill="#174f90", font=("Segoe UI", 11, "bold"))
        if self.snake_score_label is not None:
            self.snake_score_label.configure(text=f"Счёт: {self.snake_score}")

    def _reset_snake(self) -> None:
        self.snake_body = [(6, 4), (5, 4), (4, 4)]
        self.snake_direction = (1, 0)
        self.snake_next_direction = (1, 0)
        self.snake_score = 0
        self.snake_over = False
        self.snake_food = self._new_snake_food()
        self._draw_snake()
        if self.snake_board is not None:
            self.snake_board.focus_set()
        self._schedule_snake_tick()

    def _turn_snake(self, direction: tuple[int, int]) -> None:
        if self.snake_active and direction != (-self.snake_direction[0], -self.snake_direction[1]):
            self.snake_next_direction = direction
        if self.snake_board is not None:
            self.snake_board.focus_set()

    def _snake_key(self, event: Any) -> str | None:
        if not self.snake_active or event.widget == self.entry_widget:
            return None
        if event.widget.winfo_class() in {"Entry", "Text", "TEntry", "Spinbox"}:
            return None
        directions = {
            "up": (0, -1), "w": (0, -1), "down": (0, 1), "s": (0, 1),
            "left": (-1, 0), "a": (-1, 0), "right": (1, 0), "d": (1, 0),
        }
        direction = directions.get(str(event.keysym).lower())
        if direction is not None:
            self._turn_snake(direction)
            return "break"
        return None

    def _schedule_snake_tick(self) -> None:
        if self.snake_active and not self.snake_over and self.root is not None and self.snake_after is None:
            self.snake_after = self.root.after(170, self._tick_snake)

    def _tick_snake(self) -> None:
        self.snake_after = None
        if not self.snake_active or self.snake_over:
            return
        self.snake_direction = self.snake_next_direction
        head_x, head_y = self.snake_body[0]
        dx, dy = self.snake_direction
        next_head = ((head_x + dx) % 14, (head_y + dy) % 8)
        eating = next_head == self.snake_food
        body = self.snake_body if eating else self.snake_body[:-1]
        if next_head in body:
            self.snake_over = True
        else:
            self.snake_body = [next_head, *body]
            if eating:
                self.snake_score += 1
                if len(self.snake_body) == 112:
                    self.snake_over = True
                else:
                    self.snake_food = self._new_snake_food()
        self._draw_snake()
        self._schedule_snake_tick()

    def _set_snake_active(self, active: bool) -> None:
        if self.snake_active == active or self.snake_frame is None:
            return
        self.snake_active = active
        if active:
            self.snake_frame.pack(side="bottom", fill="x", pady=(8, 0), before=self.history_box)
            self._reset_snake()
        else:
            if self.snake_after is not None and self.root is not None:
                self.root.after_cancel(self.snake_after)
                self.snake_after = None
            self.snake_frame.pack_forget()
        if self.bubble_open:
            self._place_chat()

    def _menu_command(self, action: Any):
        def run() -> None:
            for menu_name in ("avatar_menu", "chat_menu"):
                menu = getattr(self, menu_name, None)
                try:
                    if menu is not None:
                        menu.unpost()
                        menu.grab_release()
                except Exception:
                    pass
            if callable(action):
                action()
        return run

    def _place_badge(self) -> None:
        if self.badge_label is None:
            return
        size = max(30, min(42, int(self.avatar_w * 0.22)))
        self.badge_label.place(x=max(4, self.avatar_w - size - 12), y=14, width=size, height=size)

    def show_settings(self) -> None:
        if self.root is not None:
            self.root.after(0, self._show_settings_dialog)

    def _refresh_context_hint(self) -> None:
        if self.context_label is None:
            return
        input_text = self.input_var.get() if self.input_var is not None else ''
        signature = (_chat_file_signature(), input_text, self.runtime.get('n_ctx'),
                     self.max_tokens, self.runtime.get('context_overflow'))
        if signature == getattr(self, '_context_hint_signature', None):
            return
        state = read_chat_state()
        conversation = _find_conversation(state.get('conversations') or [], state.get('activeConversationId')) or {}
        history = [{'content': str(m.get('answer') or m.get('text') or '')} for m in (conversation.get('messages') or [])
                   if not m.get('pending') and not message_completion_error(m)]
        messages = [{'content': 'Ты локальный корпоративный помощник Local AI GPP. Отвечай кратко и по делу на русском языке.'},
                    *history, {'content': input_text}]
        context = int(self.runtime.get('n_ctx', 4096))
        estimate = estimate_context(messages, self.max_tokens)
        warning = estimate > context
        self.context_label.configure(text=f'≈ {estimate} / {context} ток. · ответ {self.max_tokens}' + (' · история сократится' if warning and self.runtime.get('context_overflow', 'trim') == 'trim' else ' · лимит!' if warning else ''),
                                     fg='#a55d10' if warning else '#66809e')
        self._context_hint_signature = signature

    def _show_context_dialog(self) -> None:
        if self.root is None or self.tk is None:
            return
        if self.context_window is not None and self.context_window.winfo_exists():
            self.context_window.lift()
            return
        tk = self.tk
        from tkinter import ttk
        win = tk.Toplevel(self.root)
        self.context_window = win
        win.title('Контекст и длина ответа')
        win.attributes('-topmost', self.always_on_top)
        win.configure(bg='#f7fbff')
        left, top, right, bottom = self._monitor_work_area()
        x, y = max(left + 12, min(right - 462, self.avatar_x - 220)), max(top + 12, min(bottom - 440, self.avatar_y))
        win.geometry(f'450x425+{x}+{y}')
        win.resizable(False, False)
        context_var = tk.StringVar(value=str(self.runtime.get('n_ctx', 4096)))
        answer_var = tk.StringVar(value=str(self.max_tokens))
        trim_var = tk.BooleanVar(value=self.runtime.get('context_overflow', 'trim') == 'trim')
        tk.Label(win, text='Контекст включает историю, вопрос и ответ.', bg='#f7fbff', fg='#17324d', font=('Segoe UI', 10, 'bold')).pack(anchor='w', padx=18, pady=(18, 10))
        fields = tk.Frame(win, bg='#f7fbff')
        fields.pack(fill='x', padx=18)
        for row, label, variable, presets in ((0, 'Контекст, токенов', context_var, (2048, 4096, 8192, 16384)),
                                              (1, 'Максимум на ответ', answer_var, (256, 512, 1024, 2048))):
            tk.Label(fields, text=label, bg='#f7fbff', fg='#17324d', font=('Segoe UI', 10)).grid(row=row, column=0, sticky='w', pady=8)
            ttk.Combobox(fields, textvariable=variable, values=presets, width=14).grid(row=row, column=1, padx=(26, 0), pady=8)
        tk.Checkbutton(win, text='Автоматически сокращать старые реплики', variable=trim_var, bg='#f7fbff', fg='#17324d', font=('Segoe UI', 10)).pack(anchor='w', padx=14, pady=8)
        tk.Label(win, text='Полная история остаётся в диалогах. Модель получает последние реплики, которые помещаются. Больший контекст требует больше памяти; для этой видеокарты начните с 4096.', wraplength=408, justify='left', bg='#f7fbff', fg='#5b6d82', font=('Segoe UI', 9)).pack(fill='x', padx=18)
        hint = tk.Label(win, text='', wraplength=408, justify='left', bg='#f7fbff', fg='#5b6d82', font=('Segoe UI', 9))
        hint.pack(fill='x', padx=18, pady=12)

        def validate(*_args: Any) -> bool:
            try:
                context, answer = int(context_var.get()), int(answer_var.get())
                validate_context(context, answer)
                hint.configure(text=f'На вопрос и историю остаётся до {context - answer - 128} токенов (до учёта оформления). При изменении контекста модель перезагрузится со следующим запросом.', fg='#5b6d82')
                return True
            except ValueError as exc:
                hint.configure(text=str(exc) if 'токен' in str(exc) else 'Введите целое число токенов.', fg='#ba3c32')
                return False

        def save() -> None:
            if not validate():
                return
            patch_runtime = {'n_ctx': int(context_var.get()), 'max_tokens': int(answer_var.get()),
                             'context_overflow': 'trim' if trim_var.get() else 'error'}
            save_button.configure(state='disabled')
            hint.configure(text='Сохраняю настройки…')

            def worker() -> None:
                try:
                    settings = self._json_request('GET', '/api/settings')
                    settings.setdefault('runtime', {}).update(patch_runtime)
                    self._json_request('PUT', '/api/settings', settings)
                except Exception as exc:
                    error = str(exc)
                    def failed() -> None:
                        if win.winfo_exists():
                            hint.configure(text=f'Не удалось сохранить: {error}', fg='#ba3c32')
                            save_button.configure(state='normal')
                    self.ui_events.put(failed)
                else:
                    def saved() -> None:
                        self.runtime = {**self.runtime, **patch_runtime}
                        self.max_tokens = patch_runtime['max_tokens']
                        self._refresh_context_hint()
                        if win.winfo_exists():
                            win.destroy()
                        self.context_window = None
                    self.ui_events.put(saved)
            threading.Thread(target=worker, daemon=True).start()

        for variable in (context_var, answer_var):
            variable.trace_add('write', validate)
        validate()
        buttons = tk.Frame(win, bg='#f7fbff')
        buttons.pack(side='bottom', fill='x', padx=18, pady=16)
        save_button = tk.Button(buttons, text='Сохранить', command=save, bg='#1d5fa8', fg='white', relief='flat', padx=14, pady=7)
        save_button.pack(side='right')
        tk.Button(buttons, text='Закрыть', command=win.destroy, bg='#dbe8f6', fg='#17324d', relief='flat', padx=14, pady=7).pack(side='right', padx=8)

    def _refresh_runtime_settings(self) -> None:
        path = MODELS_DIR / 'settings.json'
        try:
            stamp = path.stat().st_mtime_ns
            if stamp == self.runtime_settings_stamp:
                return
            settings = _read_json_file(path, {})
            if not isinstance(settings, dict) or not isinstance(settings.get('runtime', {}), dict):
                raise ValueError('Некорректная структура настроек.')
            runtime = settings.get('runtime') or {}
            self.runtime = dict(runtime)
            self.max_tokens = int(runtime.get('max_tokens', 1024))
            self.temperature = float(runtime.get('temperature', 0.2))
            self.runtime_settings_stamp = stamp
        except (OSError, ValueError, TypeError):
            pass

    def _show_settings_dialog(self) -> None:
        if self.root is None or self.tk is None:
            return
        tk = self.tk
        if self.settings_window is not None:
            try:
                self.settings_window.lift()
                return
            except Exception:
                self.settings_window = None
        win = tk.Toplevel(self.root)
        self.settings_window = win
        win.title("Настройки помощника")
        win.attributes("-topmost", self.always_on_top)
        win.configure(bg="#f7fbff")
        win.geometry("430x420+{}+{}".format(max(20, self.avatar_x - 40), max(20, self.avatar_y + 40)))
        win.resizable(False, False)
        def close_settings() -> None:
            self.settings_window = None
            win.destroy()
        win.protocol("WM_DELETE_WINDOW", close_settings)

        fields: dict[str, Any] = {}

        def add_row(row: int, label: str, key: str, from_: int, to: int) -> None:
            tk.Label(win, text=label, bg="#f7fbff", fg="#17324d", anchor="w", font=("Segoe UI", 10, "bold")).grid(row=row, column=0, sticky="w", padx=16, pady=8)
            value = tk.IntVar(value=int(getattr(self, {"avatar_width": "avatar_w", "avatar_height": "avatar_h", "chat_width": "chat_w", "chat_height": "chat_h", "font_size": "font_size"}[key])))
            spin = tk.Spinbox(win, from_=from_, to=to, textvariable=value, width=8, font=("Segoe UI", 10))
            spin.grid(row=row, column=1, sticky="e", padx=16, pady=8)
            fields[key] = value

        add_row(0, "Ширина персонажа", "avatar_width", 120, 260)
        add_row(1, "Высота персонажа", "avatar_height", 160, 340)
        add_row(2, "Ширина чата", "chat_width", 420, 760)
        add_row(3, "Высота чата", "chat_height", 260, 900)
        add_row(4, "Размер текста", "font_size", 9, 14)
        top_var = tk.BooleanVar(value=self.always_on_top)
        tk.Checkbutton(win, text="Поверх всех окон", variable=top_var, bg="#f7fbff", fg="#17324d", font=("Segoe UI", 10), anchor="w").grid(row=5, column=0, columnspan=2, sticky="w", padx=12, pady=8)

        hint = tk.Label(win, text="Размеры сохраняются рядом с EXE: assistant_state\\assistant_settings_v54.json и применяются сразу.", bg="#f7fbff", fg="#5b6d82", wraplength=380, justify="left", font=("Segoe UI", 9))
        hint.grid(row=6, column=0, columnspan=2, sticky="we", padx=16, pady=(6, 12))

        def save_and_close() -> None:
            settings = {key: int(var.get()) for key, var in fields.items()}
            settings["always_on_top"] = bool(top_var.get())
            self._apply_assistant_settings(settings)
            try:
                win.destroy()
            finally:
                self.settings_window = None

        buttons = tk.Frame(win, bg="#f7fbff")
        buttons.grid(row=7, column=0, columnspan=2, sticky="e", padx=16, pady=6)
        tk.Button(buttons, text="Сохранить", command=save_and_close, bg="#1d5fa8", fg="white", font=("Segoe UI", 10, "bold"), relief="flat", padx=14, pady=6).pack(side="left", padx=(0, 8))
        tk.Button(buttons, text="Закрыть", command=close_settings, bg="#dbe8f6", fg="#17324d", font=("Segoe UI", 10), relief="flat", padx=14, pady=6).pack(side="left")

    def _apply_assistant_settings(self, settings_patch: dict[str, Any]) -> None:
        self.settings = write_assistant_settings(settings_patch)
        self.avatar_w = int(self.settings["avatar_width"])
        self.avatar_h = int(self.settings["avatar_height"])
        self.chat_w = int(self.settings["chat_width"])
        self.chat_h = int(self.settings["chat_height"])
        self.font_size = int(self.settings["font_size"])
        self.always_on_top = bool(self.settings["always_on_top"])
        if self.root is not None:
            self.root.attributes("-topmost", self.always_on_top)
            self.root.geometry(f"{self.avatar_w}x{self.avatar_h}+{self.avatar_x}+{self.avatar_y}")
        if self.avatar_label is not None:
            self.avatar_label.place(x=0, y=0, width=self.avatar_w, height=self.avatar_h)
        self._place_badge()
        if self.chat is not None:
            self.chat.attributes("-topmost", self.always_on_top)
            self._place_chat()
        if self.tail is not None:
            self.tail.attributes("-topmost", self.always_on_top)
        if self.close_button is not None:
            self.close_button.place(x=self.chat_w - 46, y=11, width=34, height=34)
        if self.history_box is not None:
            self.history_box.configure(font=("Segoe UI", self.font_size))
            self.history_box.tag_configure("user", font=("Segoe UI", self.font_size, "bold"))
            self.history_box.tag_configure("assistant", font=("Segoe UI", self.font_size, "bold"))
        if self.entry_widget is not None:
            self.entry_widget.configure(font=("Segoe UI", max(11, self.font_size + 1)))
        self.frames = {"ready": [], "thinking": [], "speaking": [], "error": []}
        self._load_frames()
        self.frame_index = 0
        frames = self.frames.get(self.state) or self.frames.get("ready") or []
        if frames and self.avatar_label is not None:
            self.avatar_label.configure(image=frames[0])
            self.avatar_label.image = frames[0]

    def _load_frames(self) -> None:
        frames_dir = FRONTEND_DIST / "assistant" / "frames"
        fallback_files = {
            "ready": FRONTEND_DIST / "assistant" / "assistant_ready.png",
            "thinking": FRONTEND_DIST / "assistant" / "assistant_thinking.png",
            "speaking": FRONTEND_DIST / "assistant" / "assistant_answered.png",
            "error": FRONTEND_DIST / "assistant" / "assistant_thinking.png",
        }
        for state in ("ready", "thinking", "speaking", "error"):
            if state == "ready":
                # Ready must not resize/flicker between poses: keep one stable frame.
                paths = [frames_dir / "ready_0.png"]
            elif state == "speaking":
                paths = [frames_dir / f"answered_{i}.png" for i in range(4)]
            else:
                paths = [frames_dir / f"{state}_{i}.png" for i in range(4)]
            loaded = [self._load_photo(path) for path in paths if path.exists()]
            if not loaded and fallback_files[state].exists():
                loaded = [self._load_photo(fallback_files[state])]
            self.frames[state] = [item for item in loaded if item is not None]
        if not any(self.frames.values()):
            self.frames["ready"] = []

    def _avatar_is_blue_eye_pixel(self, pixel: Any) -> bool:
        try:
            r, g, b, a = pixel
            return a > 60 and b > 90 and g > 50 and b > r + 35 and b > g - 15
        except Exception:
            return False

    def _avatar_is_eye_detail_pixel(self, pixel: Any) -> bool:
        try:
            r, g, b, a = pixel
            if a <= 60:
                return False
            if self._avatar_is_blue_eye_pixel(pixel):
                return True
            # Keep pupil / eyelashes / dark eye outline intact.
            return r < 75 and g < 85 and b < 130
        except Exception:
            return False

    def _repair_avatar_eye_whites(self, image: Any) -> Any:
        """Restore eye whites even if PNG alpha was damaged by background removal.

        Earlier sprite-cutting scripts removed all near-white pixels and sometimes
        punched transparent holes through the eyes. On a transparent desktop
        window those holes show the wallpaper, so the mascot looks like it has
        empty eye sockets. This runtime pass detects blue iris pixels in the
        upper face and fills only the tiny sclera area around them, preserving
        iris/pupil/outline pixels.
        """
        if not self.Image:
            return image
        try:
            source = image.convert("RGBA")
            width, height = source.size
            pixels = source.load()
            candidates: set[tuple[int, int]] = set()
            for y in range(int(height * 0.25), int(height * 0.47)):
                for x in range(int(width * 0.10), int(width * 0.90)):
                    if self._avatar_is_blue_eye_pixel(pixels[x, y]):
                        candidates.add((x, y))

            components: list[dict[str, Any]] = []
            while candidates:
                seed = candidates.pop()
                stack = [seed]
                points: list[tuple[int, int]] = []
                while stack:
                    x, y = stack.pop()
                    points.append((x, y))
                    for nx in (x - 1, x, x + 1):
                        for ny in (y - 1, y, y + 1):
                            if (nx, ny) in candidates:
                                candidates.remove((nx, ny))
                                stack.append((nx, ny))
                if len(points) < 6:
                    continue
                xs = [item[0] for item in points]
                ys = [item[1] for item in points]
                components.append({
                    "points": points,
                    "bbox": [min(xs), min(ys), max(xs), max(ys)],
                    "cx": sum(xs) / len(xs),
                })

            components.sort(key=lambda item: item["cx"])
            groups: list[dict[str, Any]] = []
            for component in components:
                if groups and component["cx"] - groups[-1]["cx"] < max(14, width * 0.11):
                    group = groups[-1]
                    group["points"].extend(component["points"])
                    group["bbox"] = [
                        min(group["bbox"][0], component["bbox"][0]),
                        min(group["bbox"][1], component["bbox"][1]),
                        max(group["bbox"][2], component["bbox"][2]),
                        max(group["bbox"][3], component["bbox"][3]),
                    ]
                    group["cx"] = sum(point[0] for point in group["points"]) / len(group["points"])
                else:
                    groups.append({
                        "points": list(component["points"]),
                        "bbox": list(component["bbox"]),
                        "cx": component["cx"],
                    })

            if not groups:
                return source

            output = source.copy()
            output_pixels = output.load()
            for group in groups:
                x0, y0, x1, y1 = group["bbox"]
                expand_x = max(4, int(width * 0.035))
                expand_y = max(3, int(height * 0.024))
                bx0 = max(0, x0 - expand_x)
                by0 = max(0, y0 - expand_y)
                bx1 = min(width - 1, x1 + expand_x)
                by1 = min(height - 1, y1 + expand_y + 1)
                center_x = (bx0 + bx1) / 2.0
                center_y = (by0 + by1) / 2.0
                radius_x = max(5.0, (bx1 - bx0) / 2.0)
                radius_y = max(4.0, (by1 - by0) / 2.0)
                for y in range(int(by0), int(by1) + 1):
                    for x in range(int(bx0), int(bx1) + 1):
                        if ((x - center_x) / radius_x) ** 2 + ((y - center_y) / radius_y) ** 2 > 1.0:
                            continue
                        pixel = pixels[x, y]
                        if self._avatar_is_eye_detail_pixel(pixel):
                            continue
                        output_pixels[x, y] = (246, 249, 253, 255)
            return output
        except Exception:
            return image

    def _load_photo(self, path: Path) -> Any | None:
        if self.tk is None:
            return None
        try:
            if self.Image and self.ImageTk:
                image = self.Image.open(path).convert("RGBA")
                image = self._repair_avatar_eye_whites(image)
                # Tk transparent-color windows show ugly halos when semi-transparent pixels
                # are blended with the window key color. Keep mascot pixels binary-alpha.
                alpha = image.getchannel("A")
                alpha = alpha.point(lambda value: 255 if value >= 96 else 0)
                image.putalpha(alpha)
                image.thumbnail((self.avatar_w, self.avatar_h), self.Image.Resampling.NEAREST)
                canvas = self.Image.new("RGBA", (self.avatar_w, self.avatar_h), (0, 0, 0, 0))
                x = (self.avatar_w - image.width) // 2
                y = self.avatar_h - image.height - 4
                canvas.alpha_composite(image, (x, y))
                return self.ImageTk.PhotoImage(canvas)
            return self.tk.PhotoImage(file=str(path))
        except Exception:
            return None

    def _animate(self) -> None:
        if self.root is None:
            return
        state = self.state if self.state in self.frames else "ready"
        frames = self.frames.get(state) or self.frames.get("ready") or []
        if frames and self.avatar_label is not None:
            self.avatar_label.configure(image=frames[self.frame_index % len(frames)])
            self.avatar_label.image = frames[self.frame_index % len(frames)]
            self.frame_index += 1
        delay = 420 if self.state in {"thinking", "speaking"} else 780
        self.root.after(delay, self._animate)

    def _load_position(self) -> None:
        try:
            data = json.loads(assistant_position_file().read_text(encoding="utf-8"))
            self.avatar_x = int(data.get("x", self.avatar_x))
            self.avatar_y = int(data.get("y", self.avatar_y))
        except Exception:
            pass

    def _save_position(self) -> None:
        try:
            assistant_position_file().write_text(json.dumps({"x": self.avatar_x, "y": self.avatar_y}, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    def _begin_drag(self, event: Any) -> str:
        if self.root is None:
            return "break"
        self.drag_start = (int(event.x_root), int(event.y_root), int(self.avatar_x), int(self.avatar_y))
        self.drag_moved = False
        self.drag_origin_widget = getattr(event, "widget", None)
        if self.tail is not None:
            with contextlib.suppress(Exception):
                self.tail.withdraw()

        # Windows/Tk loses B1-Motion/ButtonRelease on transparent frameless windows
        # when the cursor moves fast. Poll the real OS cursor instead; this removes
        # the "hits an invisible wall" effect and makes a click a real click.
        if os.name == "nt":
            self._cancel_drag_poll()
            self.drag_poll_after = self.root.after(10, self._poll_drag)
        return "break"

    def _move_drag(self, event: Any) -> str:
        # Fallback for non-Windows dev mode. Windows EXE uses _poll_drag().
        if not self.drag_start:
            return "break"
        self._move_avatar_to_pointer(int(event.x_root), int(event.y_root))
        return "break"

    def _end_drag(self, event: Any | None = None) -> str:
        # Fallback for non-Windows dev mode. Windows EXE normally finishes in
        # _poll_drag when GetAsyncKeyState says LMB is released.
        self._finish_drag()
        return "break"

    def _poll_drag(self) -> None:
        self.drag_poll_after = None
        if self.root is None or self.drag_start is None:
            return
        point = self._cursor_position()
        if point is not None:
            self._move_avatar_to_pointer(point[0], point[1])
        if not self._left_mouse_down():
            self._finish_drag()
            return
        self.drag_poll_after = self.root.after(12, self._poll_drag)

    def _cancel_drag_poll(self) -> None:
        if self.root is not None and self.drag_poll_after:
            with contextlib.suppress(Exception):
                self.root.after_cancel(self.drag_poll_after)
        self.drag_poll_after = None

    def _cursor_position(self) -> tuple[int, int] | None:
        if os.name != "nt":
            return None
        try:
            import ctypes

            class POINT(ctypes.Structure):
                _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

            point = POINT()
            if ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
                return int(point.x), int(point.y)
        except Exception:
            return None
        return None

    def _left_mouse_down(self) -> bool:
        if os.name != "nt":
            return False
        try:
            import ctypes

            return bool(ctypes.windll.user32.GetAsyncKeyState(0x01) & 0x8000)
        except Exception:
            return False

    def _move_avatar_to_pointer(self, pointer_x: int, pointer_y: int) -> None:
        if not self.drag_start or self.root is None:
            return
        start_x, start_y, base_x, base_y = self.drag_start
        dx = pointer_x - start_x
        dy = pointer_y - start_y
        if abs(dx) + abs(dy) > 7:
            self.drag_moved = True
        self.avatar_x = base_x + dx
        self.avatar_y = base_y + dy
        self.root.geometry(f"{self.avatar_w}x{self.avatar_h}+{self.avatar_x}+{self.avatar_y}")
        if self.bubble_open:
            self._place_chat()

    def _finish_drag(self) -> None:
        if self.drag_start is None:
            return
        self._cancel_drag_poll()
        was_click = not self.drag_moved
        self.drag_start = None
        self.drag_origin_widget = None
        self._save_position()
        if was_click:
            self.toggle_chat()
            return
        if self.bubble_open:
            self._place_chat()

    def _place_chat(self) -> None:
        if self.chat is None or self.root is None:
            return
        left, top, right, bottom = self._monitor_work_area()

        gap = 24
        left_x = self.avatar_x - self.chat_w - gap
        right_x = self.avatar_x + self.avatar_w + gap
        if left_x >= left + 12:
            x = left_x
            side = "left"
        elif right_x + self.chat_w <= right - 12:
            x = right_x
            side = "right"
        else:
            # Keep the chat visible, and prefer the side with more space.
            if self.avatar_x > (left + right) / 2:
                x = max(left + 12, min(right - self.chat_w - 12, left_x))
                side = "left"
            else:
                x = max(left + 12, min(right - self.chat_w - 12, right_x))
                side = "right"

        displayed_height = self._display_chat_height()
        y = max(top + 12, min(bottom - displayed_height - 12, self.avatar_y + 8))
        self.chat_side = side
        self.chat.geometry(f"{self.chat_w}x{displayed_height}+{x}+{y}")
        self._place_resize_edges()
        if self.close_button is not None:
            self.close_button.place(x=self.chat_w - 46, y=11, width=34, height=34)
        self._place_tail(x, y, side)

    def _place_tail(self, chat_x: int, chat_y: int, side: str) -> None:
        if self.tail is None or self.tail_canvas is None or self.chat is None:
            return
        if self.drag_start is not None or not self.bubble_open:
            self.tail.withdraw()
            return

        tail_w, tail_h = 34, 46
        avatar_mid_y = self.avatar_y + max(32, self.avatar_h // 2)
        tail_y = max(chat_y + 58, min(chat_y + self._display_chat_height() - 74, avatar_mid_y - tail_h // 2))
        if side == "left":
            tail_x = chat_x + self.chat_w - 2
            points_border = [0, 7, tail_w, tail_h // 2, 0, tail_h - 7]
            points_fill = [0, 12, tail_w - 7, tail_h // 2, 0, tail_h - 12]
        else:
            tail_x = chat_x - tail_w + 2
            points_border = [tail_w, 7, 0, tail_h // 2, tail_w, tail_h - 7]
            points_fill = [tail_w, 12, 7, tail_h // 2, tail_w, tail_h - 12]

        was_hidden = not self.tail.winfo_viewable()
        self.tail.geometry(f"{tail_w}x{tail_h}+{tail_x}+{tail_y}")
        if side != self.tail_side:
            self.tail_canvas.delete("all")
            self.tail_canvas.create_polygon(points_border, fill="#0e4f97", outline="#0e4f97")
            self.tail_canvas.create_polygon(points_fill, fill="#fbfdff", outline="#fbfdff")
            self.tail_side = side
        if was_hidden:
            self.tail.deiconify()
            self.tail.lift()

    def _show_avatar_menu(self, event: Any) -> str:
        try:
            self.avatar_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.avatar_menu.grab_release()
        return "break"

    def _show_chat_menu(self, event: Any) -> str:
        try:
            self.chat_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.chat_menu.grab_release()
        return "break"

    def _toggle_topmost(self) -> None:
        if self.root is None:
            return
        current = bool(self.root.attributes("-topmost"))
        self.always_on_top = not current
        self.settings = write_assistant_settings({"always_on_top": self.always_on_top})
        self.root.attributes("-topmost", self.always_on_top)
        if self.chat is not None:
            self.chat.attributes("-topmost", self.always_on_top)
        if self.tail is not None:
            self.tail.attributes("-topmost", self.always_on_top)

    def toggle_chat(self) -> None:
        if self.bubble_open:
            self.hide_chat()
        else:
            self.show_chat()

    def show_chat(self) -> None:
        if self.root is not None:
            self.root.deiconify()
        if self.chat is not None:
            self._render_shared_history(force=True)
            self.bubble_open = True
            self._place_chat()
            self.chat.deiconify()
            self.chat.lift()
            if self.tail is not None:
                self.tail.lift()
            self.root.lift()
            try:
                if self.entry_widget is not None:
                    self.entry_widget.focus_set()
            except Exception:
                pass

    def hide_chat(self) -> None:
        if self.chat is not None:
            self.chat.withdraw()
        if self.tail is not None:
            self.tail.withdraw()
        self.bubble_open = False

    def show(self) -> None:
        self._visible = True
        if self.root is not None:
            self.root.after(0, lambda: self.root.deiconify())

    def hide(self) -> None:
        self._visible = False
        if self.root is not None:
            self.root.after(0, self.root.withdraw)
        self.hide_chat()

    def toggle_visible(self) -> None:
        if self._visible:
            self.hide()
        else:
            self.show()

    def destroy(self) -> None:
        try:
            if self.root is not None:
                self.root.after(0, self.root.destroy)
        except Exception:
            pass

    def set_state(self, state: str, title: str, status: str) -> None:
        # Workers never call Tcl/Tk, including after(). Tk drains this queue.
        self.ui_events.put(lambda: self._set_state(state, title, status))

    def _set_state(self, state: str, title: str, status: str) -> None:
        next_state = state if state in {"ready", "thinking", "speaking", "error"} else "ready"
        if next_state != self.state:
            self.frame_index = 0
        self.state = next_state
        self.status_title = title
        self.status_text = status
        if self.title_label is not None:
            self.title_label.configure(text=self.active_dialog_title or title)
        if self.status_label is not None:
            self.status_label.configure(text=f"●  {status} · v{APP_VERSION}")
        if self.badge_label is not None:
            cfg = {
                "ready": ("✓", "#25a957"),
                "thinking": ("?", "#1e67cd"),
                "speaking": ("…", "#e3a51d"),
                "error": ("!", "#c5453c"),
            }.get(self.state, ("✓", "#25a957"))
            self.badge_label.configure(text=cfg[0], bg=cfg[1])
        self._set_snake_active(self.state == "thinking")

    def _clear_history(self) -> None:
        state = clear_chat_conversation()
        self.last_chat_sync_updated_at = float(state.get("updatedAt") or time.time() * 1000.0)
        if self.history_box is not None:
            self.history_box.configure(state="normal")
            self.history_box.delete("1.0", "end")
            self.history_box.insert("end", "История очищена.\n")
            self.history_box.configure(state="disabled")

    def _insert_history_line(self, role: str, text: str, message: dict[str, Any] | None = None) -> None:
        if self.history_box is None:
            return
        message = message or {}
        clean = self._strip_prefix(role, text)
        self.history_box.insert("end", format_message_time(message) + "\n", f"timestamp-{role}")
        if role == "user":
            self.history_box.insert("end", "Вы: ", "user")
        else:
            self.history_box.insert("end", "Помощник: ", "assistant")
        self.history_box.insert("end", clean + "\n\n", f"body-{role}")
        error = message_completion_error(message)
        if error:
            self.history_box.insert('end', f'Ошибка: {error}\n\n', 'error')
        if message.get('history_dropped'):
            self.history_box.insert('end', f"В контекст не вошло {message['history_dropped']} старых реплик. Полная история сохранена.\n\n", 'timestamp-assistant')

    def _render_shared_history(self, force: bool = False) -> None:
        if self.history_box is None:
            return
        try:
            signature = _chat_file_signature()
            if not force and signature is not None and signature == getattr(self, '_last_chat_file_signature', None):
                return
            if self.reveal_message_id:
                return
            state = read_chat_state()
            self._refresh_dialog_list(state)
            updated_at = float(state.get("updatedAt") or 0.0)
            self._last_chat_file_signature = signature
            self.last_chat_sync_updated_at = updated_at
            conversations = state.get("conversations") if isinstance(state.get("conversations"), list) else []
            active_id = str(state.get("activeConversationId") or "")
            top_index = self.history_box.index("@0,0")
            follow_latest = self.history_box.yview()[1] >= 0.98 or active_id != self.last_rendered_conversation_id
            self.last_rendered_conversation_id = active_id
            active = conversations[0] if conversations else None
            for conversation in conversations:
                if isinstance(conversation, dict) and conversation.get("id") == active_id:
                    active = conversation
                    break
            messages = active.get("messages") if isinstance(active, dict) and isinstance(active.get("messages"), list) else []
            self.active_dialog_title = str(active.get("title") or "Новый диалог") if isinstance(active, dict) else "Новый диалог"
            if self.title_label is not None:
                self.title_label.configure(text=self.active_dialog_title)
            assistant_messages = [item for item in messages if isinstance(item, dict) and item.get("role") == "assistant"]
            last = assistant_messages[-1] if assistant_messages else None
            if isinstance(last, dict) and last.get("pending"):
                self.awaiting_answer = True
                self.pending_answer_id = str(last.get("id") or "")
            elif isinstance(last, dict) and str(last.get("phase") or "") != "done":
                self.awaiting_answer = False
                self.pending_answer_id = ""
            reveal_id = ""
            if isinstance(last, dict) and not last.get("pending") and str(last.get("phase") or "") == "done" and not message_completion_error(last):
                candidate_id = str(last.get("id") or "")
                if self.awaiting_answer and candidate_id and candidate_id != self.last_revealed_answer_id:
                    reveal_id = candidate_id
            reveal_text = ""
            self.history_box.configure(state="normal")
            self.history_box.delete("1.0", "end")
            if not messages:
                self.history_box.insert("end", "На связи. Напиши вопрос ниже — отвечу с уже выбранной локальной модели.\n")
            else:
                for item in messages[-80:]:
                    if not isinstance(item, dict):
                        continue
                    role = "user" if item.get("role") == "user" else "assistant"
                    text = str(item.get("answer") or item.get("text") or "Готовлю ответ...").strip()
                    if reveal_id and str(item.get("id") or "") == reveal_id:
                        reveal_text = self._strip_prefix("assistant", text)
                        self.history_box.insert("end", format_message_time(item) + "\n", "timestamp-assistant")
                        self.history_box.insert("end", "Помощник: ", "assistant")
                        continue
                    if text:
                        self._insert_history_line(role, text, item)
            if follow_latest:
                self.history_box.see("end")
            else:
                self.history_box.yview(top_index)
            self.history_box.configure(state="disabled")
            if reveal_id and reveal_text:
                self.awaiting_answer = False
                self.pending_answer_id = ""
                self.reveal_message_id = reveal_id
                self.reveal_text = reveal_text
                self.reveal_index = 0
                self._set_state("speaking", "Отвечаю", "Показываю готовый ответ")
                self._reveal_answer_step()
            else:
                self._update_state_from_shared_messages(messages)
        except Exception:
            pass

    def _reveal_answer_step(self) -> None:
        self.reveal_after = None
        if not self.reveal_message_id or self.history_box is None:
            return
        step = max(2, (len(self.reveal_text) + 279) // 280)
        next_index = min(len(self.reveal_text), self.reveal_index + step)
        follow_latest = self.history_box.yview()[1] >= 0.98
        self.history_box.configure(state="normal")
        self.history_box.insert("end", self.reveal_text[self.reveal_index:next_index], "body-assistant")
        if follow_latest:
            self.history_box.see("end")
        self.history_box.configure(state="disabled")
        self.reveal_index = next_index
        if next_index >= len(self.reveal_text):
            self.history_box.configure(state="normal")
            self.history_box.insert("end", "\n")
            self.history_box.configure(state="disabled")
            self.last_revealed_answer_id = self.reveal_message_id
            self.reveal_message_id = ""
            self.reveal_text = ""
            self._set_state("ready", "На связи", "Готов помочь")
        elif self.root is not None:
            self.reveal_after = self.root.after(25, self._reveal_answer_step)

    def _update_state_from_shared_messages(self, messages: list[Any]) -> None:
        try:
            assistant_messages = [item for item in messages if isinstance(item, dict) and item.get("role") == "assistant"]
            last = assistant_messages[-1] if assistant_messages else None
            if isinstance(last, dict) and last.get("pending"):
                self._set_state("thinking", "Думаю", str(last.get('text') or 'Готовлю ответ')[:100])
            elif isinstance(last, dict) and message_completion_error(last):
                self._set_state('error', 'Ответ не завершён', message_completion_error(last)[:100])
            elif self.state in {"thinking", "speaking", "error"}:
                self._set_state("ready", "На связи", "Готов помочь")
        except Exception:
            pass

    def _schedule_chat_sync_poll(self) -> None:
        if self.root is None:
            return
        try:
            while not self.ui_events.empty():
                with contextlib.suppress(Exception):
                    self.ui_events.get_nowait()()
            self._refresh_runtime_settings()
            self._render_shared_history(force=False)
            self._refresh_context_hint()
        except Exception:
            pass
        finally:
            with contextlib.suppress(Exception):
                self.sync_poll_after = self.root.after(500, self._schedule_chat_sync_poll)

    def _append_history(self, role: str, text: str, persist: bool = True) -> None:
        clean = self._strip_prefix(role, text)
        if persist:
            try:
                state = append_shared_message(role, clean)
                self.last_chat_sync_updated_at = float(state.get("updatedAt") or time.time() * 1000.0)
            except Exception:
                pass
        if self.history_box is None:
            return
        self.history_box.configure(state="normal")
        self._insert_history_line(role, clean)
        self.history_box.see("end")
        self.history_box.configure(state="disabled")

    def _replace_last_assistant(self, text: str) -> None:
        # Simpler and safer for Tk Text: append live chunks once, then final answer is already visible enough.
        pass

    @staticmethod
    def _strip_prefix(role: str, text: str) -> str:
        value = str(text or "").strip()
        prefixes = ["Помощник:", "Assistant:", "AI:"] if role == "assistant" else ["Вы:", "User:"]
        changed = True
        while changed:
            changed = False
            for prefix in prefixes:
                if value.lower().startswith(prefix.lower()):
                    value = value[len(prefix):].strip()
                    changed = True
        return value

    def _refresh_bootstrap(self) -> None:
        try:
            data = self._json_request("GET", "/api/bootstrap", timeout=20)
            models = [m for m in data.get("models", []) if m.get("type") == "LLM" and m.get("file_exists") is not False]
            running = next((m for m in models if m.get("status") in {"running", "warm"}), None)
            selected = running or (models[0] if models else None)
            self.model_id = str((selected or {}).get("id") or "")
            settings = data.get("settings") or {}
            runtime = settings.get("runtime") if isinstance(settings.get("runtime"), dict) else {}
            self.runtime = runtime
            self.temperature = float(runtime.get("temperature", 0.2) or 0.2)
            self.max_tokens = int(runtime.get("max_tokens", 1024) or 1024)
            if self.model_id:
                self.set_state("ready", "На связи", "Готов помочь")
            else:
                self.set_state("error", "Нет модели", "Выбери LLM в полном интерфейсе")
        except Exception as exc:
            # Не ломаем отрисовку мини-помощника из-за битого registry/models.json.
            # Чат остается открытым, а ошибка показывается только статусом.
            self.model_id = ""
            self.set_state("error", "Проверка моделей", str(exc)[:90])

    def _send_message(self) -> None:
        if self.input_var is None or self.busy_request:
            return
        text = self.input_var.get().strip()
        if not text:
            return
        if generation_lock.locked():
            self._set_state('thinking', 'Ответ в процессе', 'Дождитесь ответа; диалоги можно просматривать')
            return
        try:
            validate_context(int(self.runtime.get('n_ctx', 4096)), self.max_tokens)
        except ValueError as exc:
            self._set_state('error', 'Настройки контекста', str(exc))
            self._show_context_dialog()
            return
        try:
            state = read_chat_state()
            conversation_id = str(state.get('activeConversationId') or SHARED_CHAT_CONVERSATION_ID)
            self.busy_request = True
            self.awaiting_answer = True
            threading.Thread(target=self._send_message_worker,
                             args=(text, conversation_id, state.get('version', 0)), daemon=True).start()
        except Exception as exc:
            self.busy_request = self.awaiting_answer = False
            self._set_state('error', 'Запрос не отправлен', str(exc))
            return
        self.input_var.set('')
        if not self.bubble_open:
            self.show_chat()
        self._set_state('thinking', 'Думаю', 'Отправил запрос в общий диалог')

    def _send_message_worker(self, text: str, conversation_id: str, base_version: int | None = None) -> None:
        try:
            payload = {
                "source": "assistant",
                "message": text,
                "conversationId": conversation_id,
                "activeConversationId": conversation_id,
                "model_id": self.model_id,
                "system_prompt": "Ты локальный корпоративный помощник Local AI GPP. Отвечай кратко и по делу на русском языке.",
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
                # Keep the model's load profile (threads/batch/GPU layers), so
                # asking from the mini helper reuses a manually warmed worker.
                "runtime": {key: self.runtime[key] for key in ('n_ctx', 'context_overflow', 'enable_thinking', 'warm_policy') if key in self.runtime},
            }
            if base_version is not None:
                payload['baseVersion'] = base_version
            result = self._json_request("POST", "/api/desktop/chat-send", payload, timeout=20)
            if not result.get("ok"):
                raise RuntimeError(str(result.get("message") or "Запрос не принят."))
            # No Tk calls from this worker thread. The assistant window polls shared_chat_v65.json.
        except Exception as exc:
            message = str(exc)
            with contextlib.suppress(Exception):
                append_shared_message('assistant', f'Ошибка: {message}', message_id=f'native-error-{uuid.uuid4().hex}',
                                      conversation_id=conversation_id, base_version=base_version, pending=False, phase='error')
            def failed() -> None:
                if self.input_var is not None and not self.input_var.get():
                    self.input_var.set(text)
                self.awaiting_answer = False
                self._set_state('error', 'Запрос не отправлен', message)
            self.ui_events.put(failed)
            # No Tk calls from this worker thread. The assistant window polls shared_chat_v65.json.
        finally:
            self.busy_request = False

    def _stream_chat(self, payload: dict[str, Any], assistant_id: str) -> str:
        port = int(self.get_port() or 0)
        if not port:
            raise RuntimeError("Локальный сервер ещё не готов.")
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/chat/stream",
            data=body,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        content = ""
        first_delta = False
        decoder = codecs.getincrementaldecoder("utf-8")("replace")

        def handle_event(event: dict[str, Any]) -> str | None:
            nonlocal content, first_delta
            if event.get("type") == "delta":
                if not first_delta:
                    first_delta = True
                    self.set_state("speaking", "Отвечаю", "Печатаю ответ")
                content += str(event.get("text") or "")
                live_answer = self._strip_prefix("assistant", content) or "Модель отвечает..."
                append_shared_message("assistant", live_answer, message_id=assistant_id, pending=True, phase="typing", answer=live_answer)
            elif event.get("type") == "done":
                final_answer = str(event.get("answer") or event.get("content") or content)
                final_answer = self._strip_prefix("assistant", final_answer or "Ответ не сформировался.")
                append_shared_message("assistant", final_answer, message_id=assistant_id, pending=False, phase="done", answer=final_answer)
                return final_answer
            elif event.get("type") == "error":
                raise RuntimeError(str(event.get("message") or "Ошибка генерации"))
            return None

        try:
            with urllib.request.urlopen(request, timeout=600) as response:
                buffer = ""
                while True:
                    chunk = response.read(4096)
                    if not chunk:
                        buffer += decoder.decode(b"", final=True)
                        break
                    buffer += decoder.decode(chunk)
                    while "\n\n" in buffer:
                        frame, buffer = buffer.split("\n\n", 1)
                        for line in frame.splitlines():
                            if not line.startswith("data:"):
                                continue
                            raw = line[5:].strip()
                            if not raw:
                                continue
                            result = handle_event(json.loads(raw))
                            if result is not None:
                                return result
                # Process a final partial frame defensively.
                if buffer.strip():
                    for line in buffer.splitlines():
                        if not line.startswith("data:"):
                            continue
                        raw = line[5:].strip()
                        if not raw:
                            continue
                        result = handle_event(json.loads(raw))
                        if result is not None:
                            return result
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            try:
                data = json.loads(raw)
                detail = data.get("detail", raw)
                if isinstance(detail, dict):
                    detail = detail.get("message") or json.dumps(detail, ensure_ascii=False)
                raise RuntimeError(str(detail)) from exc
            except json.JSONDecodeError:
                raise RuntimeError(raw or str(exc)) from exc
        return content

    def _json_request(self, method: str, path: str, payload: dict[str, Any] | None = None, timeout: int = 20) -> dict[str, Any]:
        port = int(self.get_port() or 0)
        if not port:
            raise RuntimeError("Локальный сервер ещё не готов.")
        body = None
        headers: dict[str, str] = {}
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            try:
                data = json.loads(raw)
                detail = data.get("detail", raw)
                if isinstance(detail, dict):
                    detail = detail.get("message") or json.dumps(detail, ensure_ascii=False)
                raise RuntimeError(str(detail)) from exc
            except json.JSONDecodeError:
                raise RuntimeError(raw or str(exc)) from exc
        return json.loads(raw or "{}")


def create_tray_image():
    try:
        from PIL import Image, ImageDraw
        head = branding_asset("mini_agent_head_v2.png")
        if head.is_file():
            with Image.open(head) as original:
                return original.convert("RGBA").resize((64, 64), Image.Resampling.LANCZOS)
        image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((8, 8, 56, 56), radius=14, fill=(20, 90, 160, 255))
        draw.ellipse((36, 10, 58, 32), fill=(45, 205, 105, 255), outline=(255, 255, 255, 255), width=3)
        draw.line((42, 22, 48, 27, 55, 15), fill=(255, 255, 255, 255), width=4)
        draw.text((19, 25), "AI", fill=(255, 255, 255, 255))
        return image
    except Exception:
        return None


def start_tray() -> None:
    global tray_icon
    try:
        import pystray
    except Exception as exc:
        show_error(APP_NAME, f"pystray не установлен, tray-иконка не запущена.\n\n{exc}")
        return
    image = create_tray_image()
    if image is None:
        return

    def _show_assistant(_icon=None, _item=None):
        show_assistant_window()

    def _hide_assistant(_icon=None, _item=None):
        hide_assistant_window()

    def _toggle_assistant(_icon=None, _item=None):
        toggle_assistant_window()

    def _show_main(_icon=None, _item=None):
        show_main_window()

    def _hide_main(_icon=None, _item=None):
        hide_main_window()

    def _status(_icon=None, _item=None):
        show_main_window()

    def _unload(_icon=None, _item=None):
        threading.Thread(target=unload_models_from_tray, daemon=True).start()

    def _open_models(_icon=None, _item=None):
        open_folder(MODELS_DIR)

    def _open_logs(_icon=None, _item=None):
        open_folder(LOGS_DIR)

    def _clear_history(_icon=None, _item=None):
        clear_chat_conversation()
        if assistant_agent is not None:
            assistant_agent.set_state("ready", "История", "Диалог очищен")
            if assistant_agent.root is not None:
                assistant_agent.root.after(0, lambda: assistant_agent._render_shared_history(force=True))

    def _exit(_icon=None, _item=None):
        request_exit()

    tray_icon = pystray.Icon(
        "LocalAIGPP",
        image,
        APP_NAME,
        menu=pystray.Menu(
            pystray.MenuItem("Открыть Runtime", _show_main, default=True),
            pystray.MenuItem("Скрыть полный интерфейс", _hide_main),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Состояние runtime", _status),
            pystray.MenuItem("Выгрузить модели", _unload),
            pystray.MenuItem("Очистить историю диалога", _clear_history),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Открыть папку models_storage", _open_models),
            pystray.MenuItem("Открыть логи", _open_logs),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Выход", _exit),
        ),
    )
    threading.Thread(target=tray_icon.run, daemon=True).start()


def _run_desktop() -> None:
    global main_window, server, server_thread, server_port, webview_module, assistant_agent, desktop_ui_enabled
    desktop_ui_enabled = True
    from backend.app.desktop_host import register
    register(show_main=show_main_window,hide_main=hide_main_window,
             diagnostics=desktop_diagnostics,exit=lambda: threading.Thread(target=request_exit,daemon=True).start() or {'ok':True})
    configure_windows_app_identity()
    if not FRONTEND_DIST.exists():
        show_error(APP_NAME, f"Frontend bundle not found:\n{FRONTEND_DIST}")
        return

    try:
        import webview
    except Exception as exc:
        show_error(
            APP_NAME,
            "Не удалось загрузить встроенное окно WebView.\n\n"
            "Проверь, что установлен Microsoft Edge WebView2 Runtime.\n\n"
            f"Ошибка: {exc}",
        )
        return

    webview_module = webview
    try:
        server_port = find_free_port()
    except OSError:
        show_error(APP_NAME, 'Порт Local AI занят. Закрой предыдущий экземпляр или задай LOCAL_AI_GPP_PORT.')
        return
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=server_port,
            log_level="warning",
            access_log=False,
        )
    )
    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()

    if not wait_for_server(server_port):
        show_error(APP_NAME, "Локальный сервер приложения не запустился.")
        return

    url = f"http://127.0.0.1:{server_port}"
    write_instance_info(server_port)
    start_tray()

    main_window = webview.create_window(
        APP_NAME,
        url,
        width=1500,
        height=920,
        min_size=(1100, 720),
    )

    def on_closing() -> bool:
        if quitting:
            return True
        hide_main_window()
        return False

    try:
        main_window.events.closing += on_closing
    except Exception:
        pass

    try:
        app_icon = branding_asset("mini_agent_head_v2.ico")
        webview.start(debug=False, icon=str(app_icon) if app_icon.is_file() else None)
    finally:
        if not quitting:
            # webview loop ended; keep clean shutdown rather than ghost process.
            request_exit()


def main() -> None:
    from backend.app.instance_lock import InstanceAlreadyRunning, owned_instance
    try:
        with owned_instance(assistant_state_dir() / '.instance.lock'):
            if '--headless' in sys.argv:
                from backend.app.core import LOGS_DIR
                if sys.stdout is None or sys.stderr is None:
                    stream=(LOGS_DIR/'headless.log').open('a',encoding='utf-8',buffering=1)
                    if sys.stdout is None:sys.stdout=stream
                    if sys.stderr is None:sys.stderr=stream
                uvicorn.run(app,host='127.0.0.1',port=find_free_port(),log_level='warning',access_log=False)
            else:
                _run_desktop()
    except InstanceAlreadyRunning:
        # A second launch only focuses the live owner; it never runs recovery.
        with contextlib.suppress(Exception):
            info = _read_json_file(instance_file(), {})
            port = int(info.get('port') or 0)
            if 0 < port <= 65535:
                request = urllib.request.Request(f'http://127.0.0.1:{port}/api/desktop/show-main', method='POST')
                with urllib.request.urlopen(request, timeout=2) as response:
                    response.read()


if __name__ == "__main__":
    main()
