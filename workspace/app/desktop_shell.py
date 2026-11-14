"""Workspace window, tray and companion. Runtime remains a separate process."""

from __future__ import annotations
import json
import threading
import time
import webview
from workspace.app import desktop_host
from workspace.app.mini_avatar import MiniAvatar, icon_path


class WorkspaceShell:
    def __init__(self, application, server, thread, port, root):
        self.application = application
        self.server = server
        self.thread = thread
        self.root = root
        self.quitting = False
        self.tray = None
        self.quit_lock = threading.Lock()
        self.base = f"http://127.0.0.1:{port}"
        self.main = webview.create_window(
            "Local AI Workspace",
            self.base,
            width=1360,
            height=900,
            min_size=(900, 640),
            background_color="#f4f6fa",
        )
        self.avatar = MiniAvatar(
            root,
            self.toggle_mini,
            self.show_main,
            self.request_exit,
            api_base=self.base,
        )
        self.main.events.closing += self._close_main
        desktop_host.register(
            show_main=self.show_main,
            show_avatar=self.show_avatar,
            choose_folder=self.choose_folder,
            show_mini=self.show_mini,
            hide_mini=self.hide_mini,
            exit=self.request_exit,
            activity=self.activity,
        )
        self._start_tray()

    def _close_main(self):
        if self.quitting:
            return True
        self.main.hide()
        self.avatar.show()
        return False

    def show_main(self, payload=None):
        self.main.show()
        self.main.restore()
        if isinstance(payload, dict) and payload:
            data = {
                key: payload[key]
                for key in ("task_id", "project_id", "panel")
                if isinstance(payload.get(key), str)
            }
            # A message to our own page preserves unsent forms and window-local state.
            self.main.evaluate_js(
                "window.dispatchEvent(new CustomEvent('local-ai-select-task',{detail:"
                + json.dumps(data, ensure_ascii=True)
                + "}))"
            )
        return {"ok": True}

    def show_mini(self, payload=None):
        self.avatar.show()
        self.avatar.show_chat()
        return {"ok": True}

    def show_avatar(self, payload=None):
        self.avatar.show()
        return {"ok": True}

    def choose_folder(self, payload=None):
        chosen = self.main.create_file_dialog(webview.FileDialog.FOLDER)
        return {"path": chosen[0] if chosen else ""}

    def hide_mini(self, payload=None):
        self.avatar.hide_chat()
        return {"ok": True}

    def toggle_mini(self, position=None):
        self.avatar.toggle_chat()
        return {"ok": True}

    def activity(self, state):
        self.avatar.set_state(
            "awaiting_permission"
            if state == "awaiting_permission"
            else (
                "error"
                if state == "error"
                else (
                    "thinking"
                    if state in {"starting", "running", "stopping"}
                    else "ready"
                )
            )
        )

    def request_exit(self, payload=None):
        if not self.quit_lock.acquire(blocking=False):
            return {"ok": True, "stopping": True}
        self.quitting = True

        def shutdown():
            try:
                # Stop owned DSH before ASGI drains requests waiting for permissions.
                self.application.state.tasks.close()
            finally:
                self.server.should_exit = True
                self.thread.join(20)
                self.avatar.close()
                if self.tray:
                    self.tray.stop()
                self.main.destroy()

        threading.Thread(target=shutdown, daemon=False, name="workspace-exit").start()
        return {"ok": True, "stopping": True}

    def _start_tray(self):
        import pystray
        from PIL import Image

        path = icon_path().with_suffix(".png")
        with Image.open(path) as source:
            image = source.convert("RGBA").copy()
        self.tray = pystray.Icon(
            "LocalAIWorkspace",
            image,
            "Local AI Workspace",
            menu=pystray.Menu(
                pystray.MenuItem(
                    "Открыть Workspace", lambda *_: self.show_main(), default=True
                ),
                pystray.MenuItem("Мини-помощник", lambda *_: self.show_avatar()),
                pystray.MenuItem("Скрыть помощника", lambda *_: self.avatar.hide()),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Выход из Workspace", lambda *_: self.request_exit()),
            ),
        )
        threading.Thread(target=self.tray.run, daemon=True).start()

    def run(self, smoke_seconds=None):
        if smoke_seconds:

            def later():
                time.sleep(smoke_seconds)
                self.request_exit()

            threading.Thread(target=later, daemon=True).start()
        try:
            webview.start(
                debug=False, icon=str(icon_path()) if icon_path().is_file() else None
            )
        finally:
            if not self.quitting:
                self.request_exit()
            self.thread.join(25)
            if self.thread.is_alive():
                raise RuntimeError("Workspace shutdown was not confirmed.")
